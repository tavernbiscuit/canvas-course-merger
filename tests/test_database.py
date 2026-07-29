from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable
from sqlalchemy.sql import Select

from app.config import Settings
from app.database import (
    Base,
    engine_options,
    validate_database_server,
)
from app.models import AdminUser, ExecutionJob, MergeRequest


def production_settings() -> Settings:
    return Settings(
        app_env="production",
        app_base_url="https://merger.example.edu",
        session_secret="x" * 32,
        token_encryption_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
        database_url="mysql+pymysql://user:password@mysql.example.edu/canvas_merger",
        canvas_base_url="https://canvas.example.edu",
        canvas_client_id="client-id",
        canvas_client_secret="client-secret",
        canvas_root_account_id=1,
        canvas_allowed_account_ids=frozenset(),
        canvas_oauth_scopes="scope",
        worker_poll_seconds=2,
        log_level="INFO",
    )


def test_production_requires_mysql_through_pymysql():
    settings = production_settings()
    settings.validate_for_server()

    for database_url in (
        "sqlite:///canvas_merger.db",
        "postgresql+psycopg://user:password@postgres/canvas_merger",
        "mysql+mysqldb://user:password@mysql/canvas_merger",
    ):
        with pytest.raises(RuntimeError, match="MySQL through the PyMySQL driver"):
            replace(settings, database_url=database_url).validate_for_server()


def test_engine_options_are_backend_specific():
    assert engine_options("sqlite:///canvas_merger.db") == {
        "pool_pre_ping": True,
        "connect_args": {"check_same_thread": False},
    }
    assert engine_options("mysql+pymysql://user:password@mysql/canvas_merger") == {
        "pool_pre_ping": True,
        "pool_recycle": 3600,
    }


def test_utc_timestamps_round_trip_as_aware_values_on_sqlite():
    test_engine = create_engine("sqlite://", **engine_options("sqlite://"))
    Base.metadata.create_all(test_engine)
    eastern = datetime(
        2026,
        7,
        29,
        9,
        30,
        15,
        123456,
        tzinfo=timezone(timedelta(hours=-5)),
    )

    try:
        with Session(test_engine) as db:
            admin = AdminUser(
                canvas_user_id=7,
                name="Admin User",
                created_at=eastern,
                last_login_at=eastern,
            )
            db.add(admin)
            db.commit()
            admin_id = admin.id

        with Session(test_engine) as db:
            loaded = db.get(AdminUser, admin_id)
            assert loaded is not None
            assert loaded.created_at.tzinfo is UTC
            assert loaded.created_at == eastern.astimezone(UTC)
            assert loaded.created_at.microsecond == 123456
    finally:
        Base.metadata.drop_all(test_engine)
        test_engine.dispose()


def test_mysql_schema_uses_required_storage_and_timestamp_features():
    dialect = mysql.dialect()
    statements = [
        str(CreateTable(table).compile(dialect=dialect)) for table in Base.metadata.sorted_tables
    ]

    assert all("ENGINE=InnoDB" in statement for statement in statements)
    assert all("CHARSET=utf8mb4" in statement for statement in statements)
    assert all("COLLATE utf8mb4_0900_ai_ci" in statement for statement in statements)
    assert "DATETIME(6)" in next(
        statement for statement in statements if "CREATE TABLE execution_job" in statement
    )


def test_mysql_job_claim_query_uses_skip_locked():
    statement: Select[tuple[ExecutionJob]] = (
        select(ExecutionJob)
        .where(ExecutionJob.status == "pending")
        .order_by(ExecutionJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )

    compiled = str(statement.compile(dialect=mysql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in compiled


class FakeEngine:
    def __init__(self, version: tuple[int, ...], *, is_mariadb: bool = False):
        self.dialect = SimpleNamespace(name="mysql")
        self.connection = SimpleNamespace(
            dialect=SimpleNamespace(
                name="mysql",
                server_version_info=version,
                is_mariadb=is_mariadb,
            )
        )

    def connect(self):
        connection = self.connection

        class ConnectionContext:
            def __enter__(self):
                return connection

            def __exit__(self, *_):
                return None

        return ConnectionContext()


def test_database_server_validation_requires_mysql_8_and_rejects_mariadb():
    validate_database_server(FakeEngine((8, 0, 42)))

    with pytest.raises(RuntimeError, match="MySQL 8.0 or newer"):
        validate_database_server(FakeEngine((5, 7, 44)))
    with pytest.raises(RuntimeError, match="MariaDB is not supported"):
        validate_database_server(FakeEngine((10, 11, 0), is_mariadb=True))


def test_models_remain_queryable_after_timestamp_type_change(db):
    admin = AdminUser(canvas_user_id=72, name="Database Admin")
    merge_request = MergeRequest(
        admin=admin,
        faculty_identity="faculty@example.edu",
        external_reference="TICKET-DATABASE",
    )
    db.add_all([admin, merge_request])
    db.commit()

    loaded = db.scalar(
        select(MergeRequest).where(MergeRequest.external_reference == "TICKET-DATABASE")
    )
    assert loaded is not None
    assert loaded.created_at.tzinfo is UTC
