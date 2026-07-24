from __future__ import annotations

from datetime import timedelta

from app.config import get_settings
from app.execution import ExecutionService, queue_group, recover_stale_jobs
from app.intake import IntakeRow
from app.models import (
    AdminUser,
    GroupStatus,
    ItemStatus,
    JobStatus,
    OAuthCredential,
    utcnow,
)
from app.workflow import ValidationService, create_request
from tests.fakes import FakeCanvas, canvas_fixture

ROWS = [
    IntakeRow("alpha", "2026.fall.clj.101.12345"),
    IntakeRow("alpha", "2026.fall.eng.101.23456"),
]


def prepared(db, canvas: FakeCanvas):
    admin = AdminUser(canvas_user_id=7, name="Admin User")
    db.add(admin)
    db.flush()
    db.add(
        OAuthCredential(
            admin_id=admin.id,
            encrypted_access_token="test",
            encrypted_refresh_token=None,
        )
    )
    request = create_request(
        db,
        admin=admin,
        faculty_identity="faculty@example.edu",
        external_reference="TICKET-EXEC",
        rows=ROWS,
    )
    ValidationService(get_settings(), canvas).validate_request(db, request)
    job = queue_group(db, group=request.groups[0], admin=admin)
    return admin, request, job


def service_with_fake(monkeypatch, canvas: FakeCanvas):
    service = ExecutionService(get_settings())
    monkeypatch.setattr(service.oauth, "access_token", lambda credential, db: "token")
    monkeypatch.setattr("app.execution.CanvasClient", lambda *args, **kwargs: canvas)
    return service


def test_partial_failure_retains_destination_and_can_retry(db, monkeypatch):
    canvas = canvas_fixture()
    canvas.crosslist_failures.add("2026.fall.eng.101.23456")
    _, request, job = prepared(db, canvas)

    service_with_fake(monkeypatch, canvas).run_job(db, job.id)
    group = request.groups[0]
    assert group.destination_course_id == 9001
    assert group.status == GroupStatus.COMPLETED_WITH_EXCEPTIONS.value
    assert len(canvas.created) == 1
    assert {item.status for item in group.items} == {
        ItemStatus.SUCCEEDED.value,
        ItemStatus.FAILED_RETRYABLE.value,
    }

    canvas.crosslist_failures.clear()
    retry = queue_group(db, group=group, admin=request.admin, retry_failed_only=True)
    service_with_fake(monkeypatch, canvas).run_job(db, retry.id)
    assert group.status == GroupStatus.COMPLETED.value
    assert len(canvas.created) == 1
    assert all(item.status == ItemStatus.SUCCEEDED.value for item in group.items)


def test_stale_section_blocks_before_destination_creation(db, monkeypatch):
    canvas = canvas_fixture()
    _, request, job = prepared(db, canvas)
    canvas.sections["2026.fall.clj.101.12345"]["nonxlist_course_id"] = 201
    canvas.sections["2026.fall.clj.101.12345"]["course_id"] = 999

    service_with_fake(monkeypatch, canvas).run_job(db, job.id)
    group = request.groups[0]
    assert group.destination_course_id is None
    assert group.status == GroupStatus.BLOCKED.value
    assert canvas.created == []


def test_ambiguous_creation_requires_reconciliation_and_never_crosslists(db, monkeypatch):
    canvas = canvas_fixture()
    canvas.ambiguous_create = True
    _, request, job = prepared(db, canvas)

    service_with_fake(monkeypatch, canvas).run_job(db, job.id)
    group = request.groups[0]
    assert group.status == GroupStatus.CREATION_UNKNOWN.value
    assert group.destination_course_id is None
    assert canvas.crosslisted == []
    assert job.status == JobStatus.FAILED.value


def test_stale_worker_job_is_requeued_only_after_destination_is_known(db):
    canvas = canvas_fixture()
    _, request, job = prepared(db, canvas)
    job.status = JobStatus.RUNNING.value
    job.heartbeat_at = utcnow() - timedelta(hours=1)
    request.groups[0].status = GroupStatus.EXECUTING.value
    db.commit()

    assert recover_stale_jobs(db) == 1
    assert job.status == JobStatus.PENDING.value
    assert request.groups[0].status == GroupStatus.QUEUED.value


def test_stale_job_during_course_creation_requires_reconciliation(db):
    canvas = canvas_fixture()
    _, request, job = prepared(db, canvas)
    job.status = JobStatus.RUNNING.value
    job.heartbeat_at = utcnow() - timedelta(hours=1)
    request.groups[0].status = GroupStatus.CREATION_UNKNOWN.value
    db.commit()

    assert recover_stale_jobs(db) == 0
    assert job.status == JobStatus.FAILED.value
    assert request.groups[0].status == GroupStatus.CREATION_UNKNOWN.value
