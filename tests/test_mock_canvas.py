from __future__ import annotations

from fastapi.testclient import TestClient

from app.mock_canvas import app, state


def test_mock_oauth_redirect_and_token_exchange():
    with TestClient(app, follow_redirects=False) as client:
        response = client.get(
            "/login/oauth2/auth",
            params={
                "client_id": "local-demo",
                "response_type": "code",
                "redirect_uri": "http://127.0.0.1:8000/auth/callback",
                "state": "test-state",
                "scope": "/auth/userinfo",
            },
        )
        assert response.status_code == 307
        assert response.headers["location"] == (
            "http://127.0.0.1:8000/auth/callback?code=local-demo-code&state=test-state"
        )

        token = client.post(
            "/login/oauth2/token",
            data={"grant_type": "authorization_code", "code": "local-demo-code"},
        )
        assert token.status_code == 200
        assert token.json()["access_token"] == "local-demo-access"


def test_mock_canvas_course_creation_and_crosslist():
    state.reset()
    sis_id = "2026.fall.clj.101.12345"
    with TestClient(app) as client:
        section = client.get(f"/api/v1/sections/sis_section_id:{sis_id}")
        assert section.status_code == 200
        assert section.json()["nonxlist_course_id"] is None

        created = client.post(
            "/api/v1/accounts/10/courses",
            data={
                "course[name]": "CLJ 101 (12345, 34567)",
                "course[course_code]": "CLJ 101",
                "course[enrollment_term_id]": "50",
                "offer": "false",
                "skip_course_template": "true",
            },
        )
        assert created.status_code == 200
        assert created.json()["sis_course_id"] is None
        assert created.json()["workflow_state"] == "unpublished"

        crosslisted = client.post(
            f"/api/v1/sections/sis_section_id:{sis_id}/crosslist/{created.json()['id']}"
        )
        assert crosslisted.status_code == 200
        assert crosslisted.json()["course_id"] == created.json()["id"]
        assert crosslisted.json()["nonxlist_course_id"] == 201
