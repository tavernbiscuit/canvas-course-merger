from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime

from sqlalchemy import DateTime, create_engine
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.config import get_settings


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator[datetime]):
    """Store UTC timestamps portably and always return aware UTC values."""

    impl = DateTime
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "mysql":
            return dialect.type_descriptor(MySQLDateTime(fsp=6))
        return dialect.type_descriptor(DateTime(timezone=dialect.name == "postgresql"))

    def process_bind_param(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("UTCDateTime values must include timezone information")
        value = value.astimezone(UTC)
        if dialect.name != "postgresql":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def mysql_table_options() -> dict[str, str]:
    return {
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_0900_ai_ci",
    }


def engine_options(database_url: str) -> dict[str, object]:
    backend = make_url(database_url).get_backend_name()
    options: dict[str, object] = {"pool_pre_ping": True}
    if backend == "sqlite":
        options["connect_args"] = {"check_same_thread": False}
    elif backend == "mysql":
        options["pool_recycle"] = 3600
    return options


def validate_database_server(database_engine: Engine) -> None:
    if database_engine.dialect.name != "mysql":
        return
    with database_engine.connect() as connection:
        dialect = connection.dialect
        if getattr(dialect, "is_mariadb", False):
            raise RuntimeError("MySQL 8.0 or newer is required; MariaDB is not supported")
        version = dialect.server_version_info
        if not version or version[:2] < (8, 0):
            version_label = ".".join(str(part) for part in version) if version else "unknown"
            raise RuntimeError(
                f"MySQL 8.0 or newer is required for worker locking; found {version_label}"
            )


settings = get_settings()
engine = create_engine(settings.database_url, **engine_options(settings.database_url))
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def get_db() -> Generator[Session, None, None]:
    with SessionLocal() as session:
        yield session
