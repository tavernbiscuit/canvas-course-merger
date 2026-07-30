from __future__ import annotations

import json
import logging
import secrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Annotated
from urllib.parse import quote_plus, urlparse

from fastapi import (
    Depends,
    FastAPI,
    Form,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.sessions import SessionMiddleware

from app.canvas import CanvasClient, CanvasError, OAuthService
from app.config import Settings, get_settings
from app.database import Base, engine, get_db, validate_database_server
from app.domain import DomainValidationError
from app.execution import queue_group
from app.intake import IntakeError, parse_destination_groups, parse_upload
from app.models import (
    AdminUser,
    AuditEvent,
    MergeGroup,
    MergeRequest,
    OAuthCredential,
    utcnow,
)
from app.progress import load_request_progress
from app.security import TokenCipher
from app.workflow import ValidationService, create_request, load_request, set_destination_account

logger = logging.getLogger("canvas_merger.web")
BASE_DIR = Path(__file__).resolve().parent
REQUESTS_PER_PAGE = 25
AUDIT_EVENTS_PER_PAGE = 50


@dataclass(frozen=True)
class Pagination:
    total: int
    page: int
    page_size: int

    @classmethod
    def from_total(cls, total: int, requested_page: int, page_size: int) -> Pagination:
        total_pages = max(1, (total + page_size - 1) // page_size)
        return cls(
            total=total,
            page=min(requested_page, total_pages),
            page_size=page_size,
        )

    @property
    def total_pages(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def start(self) -> int:
        return self.offset + 1 if self.total else 0

    @property
    def end(self) -> int:
        return min(self.offset + self.page_size, self.total)

    @property
    def page_numbers(self) -> tuple[int, ...]:
        start = max(1, self.page - 2)
        end = min(self.total_pages, start + 4)
        start = max(1, end - 4)
        return tuple(range(start, end + 1))


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    settings.validate_for_server()
    validate_database_server(engine)
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if settings.app_env == "development":
        Base.metadata.create_all(engine)
    yield


settings = get_settings()
app = FastAPI(
    title="Canvas Course Merger",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret,
    https_only=settings.secure_cookies,
    same_site="lax",
    max_age=8 * 60 * 60,
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")
templates.env.filters["json"] = lambda value: json.dumps(value, indent=2, sort_keys=True)
templates.env.filters["urlencode"] = quote_plus


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; "
        "img-src 'self' data:; frame-ancestors 'none'; form-action 'self'"
    )
    if settings.secure_cookies:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


def flash(request: Request, message: str, level: str = "info") -> None:
    request.session["_flash"] = {"message": message, "level": level}


def csrf_token(request: Request) -> str:
    token = request.session.get("_csrf")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["_csrf"] = token
    return token


def verify_csrf(request: Request, submitted: str) -> None:
    expected = request.session.get("_csrf")
    if not expected or not secrets.compare_digest(expected, submitted):
        raise HTTPException(status_code=403, detail="Invalid form security token")


def context(request: Request, **values: object) -> dict[str, object]:
    return {
        "request": request,
        "current_admin": getattr(request.state, "admin", None),
        "csrf_token": csrf_token(request),
        "flash": request.session.pop("_flash", None),
        "canvas_environment_label": settings.canvas_environment_label,
        "canvas_hostname": urlparse(settings.canvas_base_url).hostname or settings.canvas_base_url,
        **values,
    }


def current_admin(request: Request, db: Session = Depends(get_db)) -> AdminUser:
    admin_id = request.session.get("admin_id")
    if not admin_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    admin = db.scalar(
        select(AdminUser)
        .where(AdminUser.id == int(admin_id))
        .options(selectinload(AdminUser.credential))
    )
    if not admin or not admin.credential:
        request.session.clear()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED)
    request.state.admin = admin
    return admin


def canvas_for_admin(
    admin: AdminUser, db: Session, app_settings: Settings = settings
) -> CanvasClient:
    token = OAuthService(app_settings).access_token(admin.credential, db)
    return CanvasClient(app_settings, token)


def account_choices(admin: AdminUser, db: Session) -> list[dict[str, object]]:
    with canvas_for_admin(admin, db) as canvas:
        account_map = ValidationService(settings, canvas).allowed_accounts()
    return sorted(account_map.values(), key=lambda item: str(item.get("name", "")))


def request_reference_filter(search: str):
    escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return MergeRequest.external_reference.ilike(f"%{escaped}%", escape="\\")


def request_count_statement(search: str):
    statement = select(func.count()).select_from(MergeRequest)
    if search:
        statement = statement.where(request_reference_filter(search))
    return statement


def request_list_statement(
    search: str,
    *,
    offset: int = 0,
    limit: int | None = None,
):
    statement = select(MergeRequest).options(
        selectinload(MergeRequest.admin),
        selectinload(MergeRequest.groups),
    )
    if search:
        statement = statement.where(request_reference_filter(search))
    statement = statement.order_by(
        MergeRequest.created_at.desc(),
        MergeRequest.id.desc(),
    ).offset(offset)
    if limit is not None:
        statement = statement.limit(limit)
    return statement


def destination_group_values(form: object | None = None) -> list[dict[str, object]]:
    if form is None or not hasattr(form, "getlist"):
        return [
            {
                "index": "0",
                "source_sis_ids": ["", ""],
            }
        ]
    indexes = [
        str(value).strip()
        for value in form.getlist("group_index")  # type: ignore[attr-defined]
        if str(value).strip().isdigit()
    ]
    values: list[dict[str, object]] = []
    for position, index in enumerate(dict.fromkeys(indexes)):
        sis_ids = [
            str(value)
            for value in form.getlist(  # type: ignore[attr-defined]
                f"source_sis_id_{index}"
            )
        ]
        values.append(
            {
                "index": str(position),
                "source_sis_ids": sis_ids or ["", ""],
            }
        )
    return values or destination_group_values()


@app.exception_handler(status.HTTP_401_UNAUTHORIZED)
async def unauthorized(request: Request, _: HTTPException):
    if request.url.path.startswith("/auth/"):
        return PlainTextResponse("Unauthorized", status_code=401)
    return RedirectResponse("/course-merger/auth/login", status_code=303)


@app.exception_handler(404)
async def not_found(request: Request, _: HTTPException):
    return templates.TemplateResponse(
        request,
        "error.html",
        context(request, title="Not found", message="That record does not exist."),
        status_code=404,
    )


@app.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready")
def ready(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ready"}


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    if request.session.get("admin_id"):
        return RedirectResponse("/course-merger/requests", status_code=303)
    return templates.TemplateResponse(request, "login.html", context(request))


@app.get("/auth/login")
def login(request: Request):
    if not settings.canvas_client_id:
        return templates.TemplateResponse(
            request,
            "error.html",
            context(
                request,
                title="Canvas OAuth is not configured",
                message="Set CANVAS_CLIENT_ID and CANVAS_CLIENT_SECRET before signing in.",
            ),
            status_code=503,
        )
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state
    return RedirectResponse(OAuthService(settings).authorization_url(state), status_code=302)


@app.get("/auth/callback")
def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    expected = request.session.pop("oauth_state", None)
    if error:
        raise HTTPException(status_code=401, detail=f"Canvas authorization was denied: {error}")
    if not code or not state or not expected or not secrets.compare_digest(state, expected):
        raise HTTPException(status_code=403, detail="Invalid Canvas OAuth callback state")
    oauth = OAuthService(settings)
    payload = oauth.exchange_code(code)
    access_token = payload["access_token"]
    with CanvasClient(settings, access_token) as canvas:
        profile = canvas.current_user()
        manageable = canvas.manageable_accounts()
    manageable_ids = {int(account["id"]) for account in manageable}
    if settings.canvas_allowed_account_ids:
        manageable_ids &= settings.canvas_allowed_account_ids
    if not manageable_ids:
        raise HTTPException(
            status_code=403,
            detail="Your Canvas account cannot manage any configured destination account.",
        )

    canvas_user_id = int(profile["id"])
    admin = db.scalar(select(AdminUser).where(AdminUser.canvas_user_id == canvas_user_id))
    if not admin:
        admin = AdminUser(
            canvas_user_id=canvas_user_id,
            name=profile.get("name") or profile.get("short_name") or str(canvas_user_id),
            email=profile.get("primary_email") or profile.get("email"),
        )
        db.add(admin)
        db.flush()
    admin.name = profile.get("name") or admin.name
    admin.email = profile.get("primary_email") or profile.get("email") or admin.email
    admin.last_login_at = utcnow()
    cipher = TokenCipher(settings)
    credential = admin.credential
    if not credential:
        credential = OAuthCredential(admin_id=admin.id, encrypted_access_token="")
        db.add(credential)
    credential.encrypted_access_token = cipher.encrypt(access_token) or ""
    credential.encrypted_refresh_token = cipher.encrypt(payload.get("refresh_token"))
    credential.expires_at = utcnow() + timedelta(seconds=int(payload.get("expires_in", 3600)))
    credential.scopes = payload.get("scope")
    credential.updated_at = utcnow()
    db.commit()
    request.session.clear()
    request.session["admin_id"] = admin.id
    flash(request, f"Signed in as {admin.name}.")
    return RedirectResponse("/course-merger/requests", status_code=303)


@app.post("/auth/logout")
def logout(
    request: Request,
    csrf: Annotated[str, Form()],
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf)
    admin_id = request.session.get("admin_id")
    if admin_id:
        admin = db.scalar(
            select(AdminUser)
            .where(AdminUser.id == int(admin_id))
            .options(selectinload(AdminUser.credential))
        )
        if admin and admin.credential:
            OAuthService(settings).revoke(admin.credential)
            db.delete(admin.credential)
            db.commit()
    request.session.clear()
    return RedirectResponse("/course-merger/", status_code=303)


@app.get("/requests", response_class=HTMLResponse)
def request_list(
    request: Request,
    search: Annotated[str, Query(alias="q", max_length=255)] = "",
    page: Annotated[int, Query(ge=1)] = 1,
    admin: AdminUser = Depends(current_admin),
    db: Session = Depends(get_db),
):
    search = search.strip()
    total = int(db.scalar(request_count_statement(search)) or 0)
    pagination = Pagination.from_total(total, page, REQUESTS_PER_PAGE)
    requests = db.scalars(
        request_list_statement(
            search,
            offset=pagination.offset,
            limit=pagination.page_size,
        )
    ).all()
    return templates.TemplateResponse(
        request,
        "requests.html",
        context(
            request,
            requests=requests,
            search=search,
            pagination=pagination,
        ),
    )


@app.get("/requests/new", response_class=HTMLResponse)
def request_new(
    request: Request,
    _: AdminUser = Depends(current_admin),
):
    return templates.TemplateResponse(
        request,
        "request_new.html",
        context(
            request,
            form_groups=destination_group_values(),
        ),
    )


@app.post("/requests")
async def request_create(
    request: Request,
    admin: AdminUser = Depends(current_admin),
    db: Session = Depends(get_db),
):
    form = await request.form()
    faculty_identity = str(form.get("faculty_identity") or "")
    external_reference = str(form.get("external_reference") or "")
    verify_csrf(request, str(form.get("csrf") or ""))
    form_groups = destination_group_values(form)
    try:
        upload = form.get("upload")
        if upload and getattr(upload, "filename", ""):
            content_bytes = await upload.read(5 * 1024 * 1024 + 1)
            if len(content_bytes) > 5 * 1024 * 1024:
                raise IntakeError("Uploads are limited to 5 MB")
            rows = parse_upload(upload.filename, content_bytes)
        else:
            rows = parse_destination_groups(form)
        merge_request = create_request(
            db,
            admin=admin,
            faculty_identity=faculty_identity,
            external_reference=external_reference,
            rows=rows,
        )
        with canvas_for_admin(admin, db) as canvas:
            ValidationService(settings, canvas).validate_request(
                db,
                merge_request,
                admin_id=admin.id,
            )
        flash(request, "Request saved and validated.")
        return RedirectResponse(
            f"/course-merger/requests/{merge_request.id}",
            status_code=303,
        )
    except (IntakeError, DomainValidationError, CanvasError, ValueError) as exc:
        db.rollback()
        return templates.TemplateResponse(
            request,
            "request_new.html",
            context(
                request,
                error=str(exc),
                faculty_identity=faculty_identity,
                external_reference=external_reference,
                form_groups=form_groups,
            ),
            status_code=422,
        )


@app.get("/intake-template.csv")
def intake_template(_: AdminUser = Depends(current_admin)):
    return PlainTextResponse(
        "merge_group_key,source_sis_id\n"
        "destination-clj-eng-101,2026.fall.clj.101.12345\n"
        "destination-clj-eng-101,2026.fall.eng.101.23456\n",
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="canvas-merge-intake.csv"'},
    )


@app.get("/requests/{request_id}", response_class=HTMLResponse)
def request_detail(
    request_id: int,
    request: Request,
    admin: AdminUser = Depends(current_admin),
    db: Session = Depends(get_db),
):
    merge_request = load_request(db, request_id)
    if not merge_request:
        raise HTTPException(status_code=404)
    accounts: list[dict[str, object]] = []
    try:
        accounts = account_choices(admin, db)
    except CanvasError as exc:
        flash(request, f"Canvas account choices are unavailable: {exc}", "error")
    return templates.TemplateResponse(
        request,
        "request_detail.html",
        context(request, merge_request=merge_request, accounts=accounts),
    )


@app.get("/api/requests/{request_id}/progress")
def request_progress(
    request_id: int,
    _: AdminUser = Depends(current_admin),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    progress = load_request_progress(db, request_id)
    if progress is None:
        raise HTTPException(status_code=404)
    return progress


@app.post("/requests/{request_id}/validate")
def request_validate(
    request_id: int,
    request: Request,
    csrf: Annotated[str, Form()],
    admin: AdminUser = Depends(current_admin),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf)
    merge_request = load_request(db, request_id)
    if not merge_request:
        raise HTTPException(status_code=404)
    try:
        with canvas_for_admin(admin, db) as canvas:
            ValidationService(settings, canvas).validate_request(
                db,
                merge_request,
                admin_id=admin.id,
            )
        flash(request, "Live Canvas validation completed.")
    except CanvasError as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return RedirectResponse(f"/course-merger/requests/{request_id}", status_code=303)


@app.post("/groups/{group_id}/destination")
def destination_select(
    group_id: int,
    request: Request,
    destination_account_id: Annotated[int, Form()],
    csrf: Annotated[str, Form()],
    admin: AdminUser = Depends(current_admin),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf)
    group = db.scalar(
        select(MergeGroup)
        .where(MergeGroup.id == group_id)
        .options(selectinload(MergeGroup.items), selectinload(MergeGroup.request))
    )
    if not group:
        raise HTTPException(status_code=404)
    try:
        set_destination_account(
            db,
            group=group,
            account_id=destination_account_id,
            admin_id=admin.id,
        )
        with canvas_for_admin(admin, db) as canvas:
            ValidationService(settings, canvas).validate_group(db, group, admin_id=admin.id)
            db.commit()
        flash(request, "Destination selected and group revalidated.")
    except (CanvasError, ValueError) as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return RedirectResponse(
        f"/course-merger/requests/{group.request_id}",
        status_code=303,
    )


@app.post("/groups/{group_id}/execute")
def execute_group(
    group_id: int,
    request: Request,
    csrf: Annotated[str, Form()],
    confirm_name: Annotated[str, Form()],
    admin: AdminUser = Depends(current_admin),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf)
    group = db.scalar(
        select(MergeGroup)
        .where(MergeGroup.id == group_id)
        .options(selectinload(MergeGroup.items), selectinload(MergeGroup.request))
    )
    if not group:
        raise HTTPException(status_code=404)
    if confirm_name != group.destination_name:
        flash(request, "Confirmation did not match the generated destination name.", "error")
    else:
        try:
            queue_group(db, group=group, admin=admin)
            flash(request, "Destination course group queued for execution.")
        except ValueError as exc:
            db.rollback()
            flash(request, str(exc), "error")
    return RedirectResponse(
        f"/course-merger/requests/{group.request_id}",
        status_code=303,
    )


@app.post("/groups/{group_id}/retry")
def retry_group(
    group_id: int,
    request: Request,
    csrf: Annotated[str, Form()],
    admin: AdminUser = Depends(current_admin),
    db: Session = Depends(get_db),
):
    verify_csrf(request, csrf)
    group = db.scalar(
        select(MergeGroup)
        .where(MergeGroup.id == group_id)
        .options(selectinload(MergeGroup.items), selectinload(MergeGroup.request))
    )
    if not group:
        raise HTTPException(status_code=404)
    try:
        queue_group(db, group=group, admin=admin, retry_failed_only=True)
        flash(request, "Failed sections queued for reconciliation and retry.")
    except ValueError as exc:
        db.rollback()
        flash(request, str(exc), "error")
    return RedirectResponse(
        f"/course-merger/requests/{group.request_id}",
        status_code=303,
    )


@app.get("/audit", response_class=HTMLResponse)
def audit_log(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    _: AdminUser = Depends(current_admin),
    db: Session = Depends(get_db),
):
    total = int(db.scalar(select(func.count()).select_from(AuditEvent)) or 0)
    pagination = Pagination.from_total(total, page, AUDIT_EVENTS_PER_PAGE)
    events = db.scalars(
        select(AuditEvent)
        .options(
            selectinload(AuditEvent.admin),
            selectinload(AuditEvent.request),
        )
        .order_by(
            AuditEvent.created_at.desc(),
            AuditEvent.id.desc(),
        )
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()
    return templates.TemplateResponse(
        request,
        "audit.html",
        context(request, events=events, pagination=pagination),
    )
