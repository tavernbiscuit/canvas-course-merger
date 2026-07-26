from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main


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
        main.context(request, requests=[merge_request]),
    )
    html = response.body.decode()

    assert "Entered by" in html
    assert "Admin User" in html
    assert "admin-user@example.edu" in html


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
        main.context(request, events=[event]),
    )
    html = response.body.decode()

    assert 'href="/requests/6">TICKET-12345</a>' in html
    assert 'href="/requests/6">6</a>' not in html
    assert "Admin User" in html
    assert "admin-user@example.edu" in html
