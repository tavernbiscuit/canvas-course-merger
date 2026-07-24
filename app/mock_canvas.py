"""Local-only Canvas simulator for end-to-end development.

Run with:
    uvicorn app.mock_canvas:app --host 127.0.0.1 --port 9000

This is deliberately small and unauthenticated. Never expose it to a network.
"""

from __future__ import annotations

from copy import deepcopy
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse

ROOT_ACCOUNT_ID = 1
TERM = {"id": 50, "name": "Fall 2026"}


def _seed_data() -> tuple[
    dict[int, dict[str, object]],
    dict[int, dict[str, object]],
    dict[str, dict[str, object]],
]:
    accounts: dict[int, dict[str, object]] = {
        1: {
            "id": 1,
            "name": "Example University",
            "root_account_id": None,
            "parent_account_id": None,
        },
        10: {
            "id": 10,
            "name": "Criminal Justice",
            "root_account_id": ROOT_ACCOUNT_ID,
            "parent_account_id": ROOT_ACCOUNT_ID,
            "sis_account_id": "CLJ",
        },
        20: {
            "id": 20,
            "name": "English",
            "root_account_id": ROOT_ACCOUNT_ID,
            "parent_account_id": ROOT_ACCOUNT_ID,
            "sis_account_id": "ENG",
        },
    }
    course_specs = (
        (201, "2026.fall.clj.101.12345", 10, "CLJ 101 (12345)"),
        (202, "2026.fall.clj.101.34567", 10, "CLJ 101 (34567)"),
        (203, "2026.fall.eng.101.23456", 20, "ENG 101 (23456)"),
        (204, "2026.fall.eng.102.45678", 20, "ENG 102 (45678)"),
    )
    courses: dict[int, dict[str, object]] = {}
    sections: dict[str, dict[str, object]] = {}
    for course_id, sis_id, account_id, name in course_specs:
        courses[course_id] = {
            "id": course_id,
            "name": name,
            "course_code": name.split(" (", maxsplit=1)[0],
            "sis_course_id": sis_id,
            "account_id": account_id,
            "term": deepcopy(TERM),
            "workflow_state": "available",
        }
        sections[sis_id] = {
            "id": course_id + 1000,
            "name": name,
            "course_id": course_id,
            "sis_section_id": sis_id,
            "sis_course_id": sis_id,
            "nonxlist_course_id": None,
        }
    return accounts, courses, sections


class MockCanvasState:
    def __init__(self) -> None:
        self.lock = Lock()
        self.reset()

    def reset(self) -> None:
        self.accounts, self.courses, self.sections = _seed_data()
        self.next_course_id = 9001


state = MockCanvasState()
app = FastAPI(
    title="Canvas Course Merger Local Simulator",
    description="Local-only, unauthenticated Canvas API subset.",
)


def _sis_value(identifier: str, prefix: str) -> str:
    expected = f"{prefix}:"
    if not identifier.startswith(expected):
        raise HTTPException(status_code=404, detail=f"Expected {expected} identifier")
    return identifier.removeprefix(expected)


@app.get("/")
def index() -> dict[str, object]:
    return {
        "service": "Canvas Course Merger local simulator",
        "warning": "Local development only. Never expose this server to a network.",
        "seeded_sections": sorted(state.sections),
    }


@app.get("/login/oauth2/auth")
def authorize(redirect_uri: str, state: str, client_id: str) -> RedirectResponse:
    if client_id != "local-demo":
        raise HTTPException(status_code=401, detail="Unknown local client")
    parsed = urlsplit(redirect_uri)
    query = dict(parse_qsl(parsed.query))
    query.update({"code": "local-demo-code", "state": state})
    callback = urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )
    return RedirectResponse(callback)


@app.post("/login/oauth2/token")
def token(
    grant_type: str = Form(),
    code: str | None = Form(default=None),
    refresh_token: str | None = Form(default=None),
) -> dict[str, object]:
    if grant_type == "authorization_code" and code != "local-demo-code":
        raise HTTPException(status_code=401, detail="Invalid local authorization code")
    if grant_type == "refresh_token" and refresh_token != "local-demo-refresh":
        raise HTTPException(status_code=401, detail="Invalid local refresh token")
    if grant_type not in {"authorization_code", "refresh_token"}:
        raise HTTPException(status_code=400, detail="Unsupported grant type")
    return {
        "access_token": "local-demo-access",
        "refresh_token": "local-demo-refresh",
        "token_type": "Bearer",
        "expires_in": 3600,
        "scope": "/auth/userinfo",
    }


@app.delete("/login/oauth2/token", status_code=204)
def revoke() -> None:
    return None


@app.get("/api/v1/users/self/profile")
def profile() -> dict[str, object]:
    return {
        "id": 7001,
        "name": "Local Canvas Admin",
        "short_name": "Local Admin",
        "primary_email": "local-admin@example.edu",
    }


@app.get("/api/v1/manageable_accounts")
def manageable_accounts() -> list[dict[str, object]]:
    return [deepcopy(state.accounts[10]), deepcopy(state.accounts[20])]


@app.get("/api/v1/accounts/{account_id}/permissions")
def permissions(account_id: int) -> dict[str, bool]:
    if account_id not in state.accounts:
        raise HTTPException(status_code=404, detail="Account not found")
    return {"manage_courses": True, "manage_sections": True, "read_sis": True}


@app.get("/api/v1/accounts/{account_id}")
def account(account_id: int) -> dict[str, object]:
    if account_id not in state.accounts:
        raise HTTPException(status_code=404, detail="Account not found")
    return deepcopy(state.accounts[account_id])


@app.get("/api/v1/sections/{section_identifier}")
def section(section_identifier: str) -> dict[str, object]:
    sis_id = _sis_value(section_identifier, "sis_section_id")
    if sis_id not in state.sections:
        raise HTTPException(status_code=404, detail="Section not found")
    return deepcopy(state.sections[sis_id])


@app.get("/api/v1/courses/{course_id}")
def course(course_id: int) -> dict[str, object]:
    if course_id not in state.courses:
        raise HTTPException(status_code=404, detail="Course not found")
    return deepcopy(state.courses[course_id])


@app.post("/api/v1/accounts/{account_id}/courses")
async def create_course(account_id: int, request: Request) -> JSONResponse:
    if account_id not in state.accounts:
        raise HTTPException(status_code=404, detail="Account not found")
    form = await request.form()
    name = str(form.get("course[name]") or "")
    course_code = str(form.get("course[course_code]") or "")
    term_id = int(str(form.get("course[enrollment_term_id]") or 0))
    if not name or not course_code or term_id != TERM["id"]:
        raise HTTPException(status_code=422, detail="Invalid mock course payload")
    with state.lock:
        course_id = state.next_course_id
        state.next_course_id += 1
        created = {
            "id": course_id,
            "name": name,
            "course_code": course_code,
            "sis_course_id": None,
            "account_id": account_id,
            "term": deepcopy(TERM),
            "workflow_state": "unpublished",
        }
        state.courses[course_id] = created
    return JSONResponse(created, headers={"X-Request-Context-Id": f"mock-create-{course_id}"})


@app.post("/api/v1/sections/{section_identifier}/crosslist/{new_course_id}")
def crosslist(section_identifier: str, new_course_id: int) -> JSONResponse:
    sis_id = _sis_value(section_identifier, "sis_section_id")
    if sis_id not in state.sections:
        raise HTTPException(status_code=404, detail="Section not found")
    if new_course_id not in state.courses:
        raise HTTPException(status_code=404, detail="Destination course not found")
    with state.lock:
        current = state.sections[sis_id]
        if current["nonxlist_course_id"] is not None:
            raise HTTPException(status_code=409, detail="Section is already cross-listed")
        current["nonxlist_course_id"] = current["course_id"]
        current["course_id"] = new_course_id
        result = deepcopy(current)
    return JSONResponse(result, headers={"X-Request-Context-Id": f"mock-xlist-{sis_id}"})


@app.post("/__mock__/reset")
def reset() -> dict[str, str]:
    with state.lock:
        state.reset()
    return {"status": "reset"}
