from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_public_home_and_health_endpoints():
    with TestClient(app) as client:
        home = client.get("/")
        assert home.status_code == 200
        assert "Safe, auditable Canvas course merges" in home.text
        assert client.get("/health/live").json() == {"status": "ok"}
        assert client.get("/health/ready").json() == {"status": "ready"}


def test_protected_page_redirects_to_canvas_login():
    with TestClient(app, follow_redirects=False) as client:
        response = client.get("/requests")
        assert response.status_code == 303
        assert response.headers["location"] == "/auth/login"
