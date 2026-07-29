from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

load_dotenv()


def _csv_ints(value: str) -> frozenset[int]:
    return frozenset(int(part.strip()) for part in value.split(",") if part.strip())


def _development_fernet_key() -> str:
    digest = hashlib.sha256(b"canvas-merger-development-only-key").digest()
    return base64.urlsafe_b64encode(digest).decode()


@dataclass(frozen=True)
class Settings:
    app_env: str
    app_base_url: str
    session_secret: str
    token_encryption_key: str
    database_url: str
    canvas_base_url: str
    canvas_client_id: str
    canvas_client_secret: str
    canvas_root_account_id: int
    canvas_allowed_account_ids: frozenset[int]
    canvas_oauth_scopes: str
    worker_poll_seconds: float
    log_level: str
    canvas_environment_label: str = ""

    @property
    def oauth_callback_url(self) -> str:
        return f"{self.app_base_url.rstrip('/')}/auth/callback"

    @property
    def secure_cookies(self) -> bool:
        return self.app_base_url.lower().startswith("https://")

    def validate_for_server(self) -> None:
        if self.app_env != "development":
            if len(self.session_secret) < 32:
                raise RuntimeError("APP_SESSION_SECRET must contain at least 32 characters")
            if not self.canvas_client_id or not self.canvas_client_secret:
                raise RuntimeError("Canvas OAuth client credentials are required")
            try:
                database_url = make_url(self.database_url)
            except ArgumentError as exc:
                raise RuntimeError("DATABASE_URL is not a valid SQLAlchemy database URL") from exc
            if database_url.drivername != "mysql+pymysql":
                raise RuntimeError(
                    "Production deployments must use MySQL through the PyMySQL driver"
                )
            if not self.app_base_url.startswith("https://"):
                raise RuntimeError("APP_BASE_URL must use HTTPS outside development")
            if not self.canvas_base_url.startswith("https://"):
                raise RuntimeError("CANVAS_BASE_URL must use HTTPS outside development")
            if self.token_encryption_key == _development_fernet_key():
                raise RuntimeError("APP_TOKEN_ENCRYPTION_KEY must be replaced")


@lru_cache
def get_settings() -> Settings:
    app_env = os.getenv("APP_ENV", "development")
    return Settings(
        app_env=app_env,
        app_base_url=os.getenv("APP_BASE_URL", "http://localhost:8000"),
        session_secret=os.getenv("APP_SESSION_SECRET", "development-session-secret-change-me"),
        token_encryption_key=os.getenv("APP_TOKEN_ENCRYPTION_KEY", _development_fernet_key()),
        database_url=os.getenv("DATABASE_URL", "sqlite:///./canvas_merger.db"),
        canvas_base_url=os.getenv("CANVAS_BASE_URL", "https://canvas.example.edu").rstrip("/"),
        canvas_client_id=os.getenv("CANVAS_CLIENT_ID", ""),
        canvas_client_secret=os.getenv("CANVAS_CLIENT_SECRET", ""),
        canvas_root_account_id=int(os.getenv("CANVAS_ROOT_ACCOUNT_ID", "1")),
        canvas_allowed_account_ids=_csv_ints(os.getenv("CANVAS_ALLOWED_ACCOUNT_IDS", "")),
        canvas_oauth_scopes=os.getenv(
            "CANVAS_OAUTH_SCOPES",
            "url:GET|/api/v1/users/:user_id/profile "
            "url:GET|/api/v1/manageable_accounts "
            "url:GET|/api/v1/accounts/:id "
            "url:GET|/api/v1/accounts/:account_id/permissions "
            "url:GET|/api/v1/sections/:id "
            "url:GET|/api/v1/courses/:id "
            "url:POST|/api/v1/accounts/:account_id/courses "
            "url:POST|/api/v1/sections/:id/crosslist/:new_course_id",
        ),
        worker_poll_seconds=float(os.getenv("WORKER_POLL_SECONDS", "2")),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        canvas_environment_label=os.getenv("CANVAS_ENVIRONMENT_LABEL", "").strip(),
    )
