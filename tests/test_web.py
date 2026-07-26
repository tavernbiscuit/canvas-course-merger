from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main
from app.models import AdminUser, AuditEvent, MergeRequest


def template_request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "root_path": "",
            "scheme": "http",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
            "app": main.app,
            "router": main.app.router,
            "session": {},
        }
    )


def test_public_home_and_health_endpoints():
    with TestClient(main.app) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "Canvas course merges, handled with confidence." in home.text
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}


def test_protected_page_redirects_to_canvas_login():
    with TestClient(main.app, follow_redirects=False) as client:
        response = client.get("/requests")
        assert response.status_code == 303
        assert response.headers["location"] == "/auth/login"
        progress = client.get("/api/requests/1/progress")
        assert progress.status_code == 303
        assert progress.headers["location"] == "/auth/login"


def test_environment_banner_shows_label_and_canvas_hostname(monkeypatch):
    monkeypatch.setattr(
        main,
        "settings",
        replace(
            main.settings,
            canvas_environment_label="Canvas Test",
            canvas_base_url="https://uic-prod.test.instructure.com",
        ),
    )

    with TestClient(main.app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Canvas Test" in response.text
    assert "uic-prod.test.instructure.com" in response.text


def test_request_detail_displays_ticket_reference_instead_of_internal_id():
    request = template_request("/requests/6")
    merge_request = SimpleNamespace(
        id=6,
        external_reference="TICKET-12345",
        faculty_identity="faculty@example.edu",
        admin=SimpleNamespace(name="Admin User"),
        groups=[],
        status="draft",
    )

    response = main.templates.TemplateResponse(
        request,
        "request_detail.html",
        main.context(request, merge_request=merge_request, accounts=[]),
    )
    html = response.body.decode()

    assert "TICKET-12345" in html
    assert "Request 6" not in html


def test_request_list_displays_the_admin_who_entered_each_request():
    request = template_request("/requests")
    merge_request = SimpleNamespace(
        id=6,
        external_reference="TICKET-12345",
        faculty_identity="faculty@example.edu",
        admin=SimpleNamespace(
            name="Admin User",
            email="admin-user@example.edu",
        ),
        groups=[],
        status="ready",
        created_at=datetime(2026, 7, 26, 12, 0, tzinfo=UTC),
    )

    response = main.templates.TemplateResponse(
        request,
        "requests.html",
        main.context(
            request,
            requests=[merge_request],
            search="",
            pagination=main.Pagination.from_total(1, 1, main.REQUESTS_PER_PAGE),
        ),
    )
    html = response.body.decode()

    assert "Entered by" in html
    assert "Admin User" in html
    assert "admin-user@example.edu" in html


def test_request_search_is_partial_case_insensitive_and_treats_wildcards_literally(db):
    admin = AdminUser(canvas_user_id=7, name="Admin User")
    db.add(admin)
    db.add_all(
        [
            MergeRequest(
                admin=admin,
                faculty_identity="faculty@example.edu",
                external_reference="INC-12345",
            ),
            MergeRequest(
                admin=admin,
                faculty_identity="faculty@example.edu",
                external_reference="REQ_100%",
            ),
            MergeRequest(
                admin=admin,
                faculty_identity="faculty@example.edu",
                external_reference="TICKET-67890",
            ),
        ]
    )
    db.commit()

    partial = db.scalars(main.request_list_statement("inc-123")).all()
    literal_wildcard = db.scalars(main.request_list_statement("_100%")).all()
    all_requests = db.scalars(main.request_list_statement("")).all()
    no_matches = db.scalars(main.request_list_statement("missing")).all()

    assert [item.external_reference for item in partial] == ["INC-12345"]
    assert [item.external_reference for item in literal_wildcard] == ["REQ_100%"]
    assert len(all_requests) == 3
    assert no_matches == []


def test_request_search_renders_query_clear_action_and_no_results_state():
    request = template_request("/requests")

    response = main.templates.TemplateResponse(
        request,
        "requests.html",
        main.context(
            request,
            requests=[],
            search="INC-123",
            pagination=main.Pagination.from_total(0, 1, main.REQUESTS_PER_PAGE),
        ),
    )
    html = response.body.decode()

    assert 'value="INC-123"' in html
    assert "No matching requests" in html
    assert "Clear search" in html
    assert 'href="/requests">Clear</a>' in html


def test_request_search_route_filters_rendered_results(db):
    admin = AdminUser(canvas_user_id=7, name="Admin User")
    db.add_all(
        [
            admin,
            MergeRequest(
                admin=admin,
                faculty_identity="faculty@example.edu",
                external_reference="INC-12345",
            ),
            MergeRequest(
                admin=admin,
                faculty_identity="faculty@example.edu",
                external_reference="TICKET-67890",
            ),
        ]
    )
    db.commit()

    def override_db():
        yield db

    main.app.dependency_overrides[main.get_db] = override_db
    main.app.dependency_overrides[main.current_admin] = lambda: admin
    try:
        with TestClient(main.app) as client:
            response = client.get("/requests", params={"q": "inc-123"})
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "INC-12345" in response.text
    assert "TICKET-67890" not in response.text


def test_pagination_clamps_pages_and_builds_a_five_page_window():
    pagination = main.Pagination.from_total(127, 99, 25)

    assert pagination.page == 6
    assert pagination.total_pages == 6
    assert pagination.offset == 125
    assert pagination.start == 126
    assert pagination.end == 127
    assert pagination.page_numbers == (2, 3, 4, 5, 6)


def test_request_list_paginates_and_preserves_ticket_search(db):
    admin = AdminUser(canvas_user_id=7, name="Admin User")
    created_at = datetime(2026, 7, 1, tzinfo=UTC)
    db.add(admin)
    db.add_all(
        [
            MergeRequest(
                admin=admin,
                faculty_identity="faculty@example.edu",
                external_reference=f"INC-{index:03d}",
                created_at=created_at + timedelta(minutes=index),
            )
            for index in range(27)
        ]
    )
    db.add(
        MergeRequest(
            admin=admin,
            faculty_identity="faculty@example.edu",
            external_reference="TICKET-OTHER",
            created_at=created_at + timedelta(days=1),
        )
    )
    db.commit()

    def override_db():
        yield db

    main.app.dependency_overrides[main.get_db] = override_db
    main.app.dependency_overrides[main.current_admin] = lambda: admin
    try:
        with TestClient(main.app) as client:
            first_page = client.get("/requests", params={"q": "INC"})
            second_page = client.get("/requests", params={"q": "INC", "page": 2})
    finally:
        main.app.dependency_overrides.clear()

    assert first_page.status_code == 200
    assert "INC-026" in first_page.text
    assert "INC-001" not in first_page.text
    assert "INC-000" not in first_page.text
    assert "TICKET-OTHER" not in first_page.text
    assert "1–25 of 27" in first_page.text
    assert "page=2&amp;q=INC" in first_page.text

    assert second_page.status_code == 200
    assert "INC-001" in second_page.text
    assert "INC-000" in second_page.text
    assert "INC-026" not in second_page.text
    assert "26–27 of 27" in second_page.text


def test_audit_log_displays_ticket_reference_instead_of_internal_id():
    request = template_request("/audit")
    merge_request = SimpleNamespace(id=6, external_reference="TICKET-12345")
    event = SimpleNamespace(
        created_at=datetime(2026, 7, 26, 12, 0, tzinfo=UTC),
        event_type="group.validated",
        admin_id=7,
        admin=SimpleNamespace(
            name="Admin User",
            email="admin-user@example.edu",
        ),
        request_id=6,
        request=merge_request,
        details={},
    )

    response = main.templates.TemplateResponse(
        request,
        "audit.html",
        main.context(
            request,
            events=[event],
            pagination=main.Pagination.from_total(1, 1, main.AUDIT_EVENTS_PER_PAGE),
        ),
    )
    html = response.body.decode()

    assert 'href="/requests/6">TICKET-12345</a>' in html
    assert 'href="/requests/6">6</a>' not in html
    assert "Admin User" in html
    assert "admin-user@example.edu" in html


def test_audit_log_paginates_events(db):
    admin = AdminUser(canvas_user_id=7, name="Admin User")
    merge_request = MergeRequest(
        admin=admin,
        faculty_identity="faculty@example.edu",
        external_reference="INC-12345",
    )
    created_at = datetime(2026, 7, 1, tzinfo=UTC)
    db.add_all([admin, merge_request])
    db.flush()
    db.add_all(
        [
            AuditEvent(
                admin_id=admin.id,
                request_id=merge_request.id,
                event_type=f"event.{index:02d}",
                details={},
                created_at=created_at + timedelta(minutes=index),
            )
            for index in range(55)
        ]
    )
    db.commit()

    def override_db():
        yield db

    main.app.dependency_overrides[main.get_db] = override_db
    main.app.dependency_overrides[main.current_admin] = lambda: admin
    try:
        with TestClient(main.app) as client:
            response = client.get("/audit", params={"page": 2})
    finally:
        main.app.dependency_overrides.clear()

    assert response.status_code == 200
    assert "event.04" in response.text
    assert "event.00" in response.text
    assert "event.54" not in response.text
    assert "51–55 of 55" in response.text
