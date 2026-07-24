from __future__ import annotations

from app.config import get_settings
from app.execution import queue_group
from app.intake import IntakeRow
from app.models import (
    AdminUser,
    GroupStatus,
    ItemStatus,
    JobStatus,
    utcnow,
)
from app.progress import load_request_progress
from app.workflow import ValidationService, create_request
from tests.fakes import canvas_fixture

ROWS = [
    IntakeRow("alpha", "2026.fall.clj.101.12345"),
    IntakeRow("alpha", "2026.fall.eng.101.23456"),
]


def queued_request(db):
    admin = AdminUser(canvas_user_id=71, name="Progress Admin")
    db.add(admin)
    db.flush()
    request = create_request(
        db,
        admin=admin,
        faculty_identity="faculty@example.edu",
        external_reference="TICKET-PROGRESS",
        rows=ROWS,
    )
    ValidationService(get_settings(), canvas_fixture()).validate_request(db, request)
    job = queue_group(db, group=request.groups[0], admin=admin)
    return request, job


def test_queued_progress_includes_position_and_execution_steps(db):
    request, job = queued_request(db)

    payload = load_request_progress(db, request.id)

    assert payload is not None
    assert payload["active"] is True
    assert payload["poll_after_ms"] == 1500
    group = payload["groups"][0]
    assert group["job_id"] == job.id
    assert group["queue_position"] == 1
    assert group["percent"] == 5
    assert group["title"] == "Queued for execution"
    assert group["steps"][0]["state"] == "active"


def test_running_progress_tracks_destination_and_section_outcomes(db):
    request, job = queued_request(db)
    group = request.groups[0]
    job.status = JobStatus.RUNNING.value
    job.started_at = utcnow()
    job.heartbeat_at = utcnow()
    group.status = GroupStatus.CREATION_UNKNOWN.value
    db.commit()

    creating = load_request_progress(db, request.id)
    assert creating is not None
    creating_group = creating["groups"][0]
    assert creating_group["title"] == "Creating the blank destination course"
    assert creating_group["steps"][2]["state"] == "active"

    group.destination_course_id = 9001
    group.status = GroupStatus.EXECUTING.value
    group.items[0].status = ItemStatus.SUCCEEDED.value
    db.commit()

    crosslisting = load_request_progress(db, request.id)
    assert crosslisting is not None
    crosslisting_group = crosslisting["groups"][0]
    assert crosslisting_group["title"] == "Cross-listing sections"
    assert crosslisting_group["counts"]["resolved"] == 1
    assert crosslisting_group["counts"]["succeeded"] == 1
    assert crosslisting_group["steps"][3]["state"] == "active"


def test_terminal_progress_reports_partial_and_reconciliation_outcomes(db):
    request, job = queued_request(db)
    group = request.groups[0]
    group.destination_course_id = 9001
    group.items[0].status = ItemStatus.SUCCEEDED.value
    group.items[1].status = ItemStatus.FAILED_RETRYABLE.value
    group.status = GroupStatus.COMPLETED_WITH_EXCEPTIONS.value
    job.status = JobStatus.SUCCEEDED.value
    job.finished_at = utcnow()
    db.commit()

    partial = load_request_progress(db, request.id)
    assert partial is not None
    partial_group = partial["groups"][0]
    assert partial["active"] is False
    assert partial_group["terminal"] is True
    assert partial_group["tone"] == "warning"
    assert partial_group["steps"][-1]["state"] == "warning"

    group.destination_course_id = None
    group.status = GroupStatus.CREATION_UNKNOWN.value
    job.status = JobStatus.FAILED.value
    job.error = "Canvas response was not received"
    db.commit()

    reconciliation = load_request_progress(db, request.id)
    assert reconciliation is not None
    reconciliation_group = reconciliation["groups"][0]
    assert reconciliation_group["tone"] == "error"
    assert reconciliation_group["title"] == "Administrator reconciliation required"
    assert reconciliation_group["steps"][2]["state"] == "error"
