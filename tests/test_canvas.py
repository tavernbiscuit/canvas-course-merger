from __future__ import annotations

import httpx
import pytest

from app.canvas import CanvasClient, CanvasError, OAuthService
from app.config import Settings


def settings() -> Settings:
    return Settings(
        app_env="test",
        app_base_url="https://merger.example.edu",
        session_secret="x" * 32,
        token_encryption_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        database_url="mysql+pymysql://unused",
        canvas_base_url="https://canvas.example.edu",
        canvas_client_id="client-id",
        canvas_client_secret="client-secret",
        canvas_root_account_id=1,
        canvas_allowed_account_ids=frozenset(),
        canvas_oauth_scopes="url:GET|/api/v1/users/:user_id/profile",
        worker_poll_seconds=2,
        log_level="INFO",
    )


def test_oauth_url_contains_state_callback_and_scope():
    url = httpx.URL(OAuthService(settings()).authorization_url("state-value"))
    assert url.path == "/login/oauth2/auth"
    assert url.params["state"] == "state-value"
    assert url.params["redirect_uri"] == "https://merger.example.edu/auth/callback"
    assert url.params["scope"] == "url:GET|/api/v1/users/:user_id/profile"


def test_create_course_is_unpublished_blank_and_has_no_sis_id():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content.decode()
        return httpx.Response(
            200,
            json={"id": 9001},
            headers={"X-Request-Context-Id": "request-1"},
        )

    with CanvasClient(settings(), "token", transport=httpx.MockTransport(handler)) as client:
        result = client.create_course(
            10, name="CLJ/ENG 101 (12345, 23456)", course_code="CLJ/ENG 101", term_id=50
        )
    body = httpx.QueryParams(captured["body"])
    assert body["offer"] == "false"
    assert body["skip_course_template"] == "true"
    assert body["course[enrollment_term_id]"] == "50"
    assert "course[sis_course_id]" not in body
    assert result.request_id == "request-1"


def test_crosslist_uses_sis_section_identifier():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        return httpx.Response(200, json={"id": 101})

    with CanvasClient(settings(), "token", transport=httpx.MockTransport(handler)) as client:
        client.crosslist_section("2026.fall.clj.101.12345", 9001)
    assert captured["path"] == (
        "/api/v1/sections/sis_section_id:2026.fall.clj.101.12345/crosslist/9001"
    )


def test_account_permissions_uses_current_granular_identifiers():
    captured = {}
    requested_permissions = (
        "manage_courses_add",
        "manage_courses_admin",
        "manage_sections_edit",
        "read_sis",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured["permissions"] = request.url.params.get_list("permissions[]")
        return httpx.Response(
            200,
            json={permission: True for permission in requested_permissions},
        )

    with CanvasClient(settings(), "token", transport=httpx.MockTransport(handler)) as client:
        result = client.account_permissions(10, requested_permissions)

    assert captured["permissions"] == list(requested_permissions)
    assert result == {permission: True for permission in requested_permissions}


def test_mutating_server_error_is_not_automatically_retried():
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, json={"message": "unknown outcome"})

    with CanvasClient(settings(), "token", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CanvasError) as raised:
            client.create_course(10, name="Test", course_code="Test", term_id=50)
    assert raised.value.ambiguous is True
    assert calls == 1
