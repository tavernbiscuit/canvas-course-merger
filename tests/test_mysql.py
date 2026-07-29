from __future__ import annotations

import os
from datetime import timedelta

import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from app.database import Base, engine_options, validate_database_server
from app.execution import claim_next_job
from app.models import (
    AdminUser,
    ExecutionJob,
    MergeGroup,
    MergeItem,
    MergeRequest,
    utcnow,
)


@pytest.fixture(scope="module")
def mysql_engine():
    database_url = os.getenv("TEST_MYSQL_DATABASE_URL")
    if not database_url:
        pytest.skip("set TEST_MYSQL_DATABASE_URL to run the live MySQL integration test")

    test_engine = create_engine(database_url, **engine_options(database_url))
    if test_engine.dialect.name != "mysql":
        pytest.fail("TEST_MYSQL_DATABASE_URL must use a MySQL database")

    validate_database_server(test_engine)
    with test_engine.connect() as connection:
        existing_tables = inspect(connection).get_table_names()
    if existing_tables:
        pytest.fail("TEST_MYSQL_DATABASE_URL must point to an empty, disposable database")

    Base.metadata.create_all(test_engine)
    try:
        yield test_engine
    finally:
        Base.metadata.drop_all(test_engine)
        test_engine.dispose()


def test_mysql_round_trip_and_worker_skip_locked(mysql_engine):
    created_at = utcnow().replace(microsecond=123456)
    with Session(mysql_engine) as db:
        admin = AdminUser(
            canvas_user_id=7001,
            name="MySQL Integration Admin",
            created_at=created_at,
            last_login_at=created_at,
        )
        merge_request = MergeRequest(
            admin=admin,
            faculty_identity="faculty@example.edu",
            external_reference="MYSQL-INTEGRATION",
        )
        first_group = MergeGroup(group_key="first")
        first_group.items.append(
            MergeItem(
                source_sis_id="2026.fall.clj.101.12345",
                snapshot={"course_id": 101, "eligible": True},
            )
        )
        second_group = MergeGroup(group_key="second")
        second_group.items.append(
            MergeItem(
                source_sis_id="2026.fall.eng.101.23456",
                snapshot={"course_id": 102, "eligible": True},
            )
        )
        merge_request.groups.extend([first_group, second_group])
        db.add(merge_request)
        db.flush()
        first_job = ExecutionJob(
            group_id=first_group.id,
            requested_by_admin_id=admin.id,
            created_at=created_at,
        )
        second_job = ExecutionJob(
            group_id=second_group.id,
            requested_by_admin_id=admin.id,
            created_at=created_at + timedelta(seconds=1),
        )
        db.add_all([first_job, second_job])
        db.commit()
        admin_id = admin.id
        first_job_id = first_job.id
        second_job_id = second_job.id

    with Session(mysql_engine) as db:
        loaded_admin = db.get(AdminUser, admin_id)
        loaded_item = db.scalar(
            select(MergeItem).where(MergeItem.source_sis_id == "2026.fall.clj.101.12345")
        )
        assert loaded_admin is not None
        assert loaded_admin.created_at == created_at
        assert loaded_admin.created_at.tzinfo is not None
        assert loaded_admin.created_at.microsecond == 123456
        assert loaded_item is not None
        assert loaded_item.snapshot == {"course_id": 101, "eligible": True}

    with Session(mysql_engine) as locker:
        locked_job = locker.scalar(
            select(ExecutionJob).where(ExecutionJob.id == first_job_id).with_for_update()
        )
        assert locked_job is not None

        with Session(mysql_engine) as worker:
            claimed_job = claim_next_job(worker)
            assert claimed_job is not None
            assert claimed_job.id == second_job_id

        locker.rollback()
