from __future__ import annotations

from app.config import get_settings
from app.intake import IntakeRow
from app.models import AdminUser, GroupStatus, ItemStatus, RequestStatus
from app.workflow import ValidationService, create_request, set_destination_account
from tests.fakes import canvas_fixture

ROWS = [
    IntakeRow("alpha", "2026.fall.clj.101.12345"),
    IntakeRow("alpha", "2026.fall.eng.101.23456"),
]


def admin(db):
    value = AdminUser(canvas_user_id=7, name="Admin User")
    db.add(value)
    db.commit()
    return value


def test_validates_and_generates_destination(db):
    request = create_request(
        db,
        admin=admin(db),
        faculty_identity="faculty@example.edu",
        external_reference="TICKET-1",
        rows=ROWS,
    )
    service = ValidationService(get_settings(), canvas_fixture())
    assert service.validate_request(db, request)
    group = request.groups[0]
    assert group.status == GroupStatus.READY.value
    assert group.destination_name == "CLJ/ENG 101 (12345, 23456)"
    assert group.destination_course_code == "CLJ/ENG 101"
    assert group.destination_account_id == 10
    assert group.enrollment_term_name == "Fall 2026"
    assert all(item.status == ItemStatus.ELIGIBLE.value for item in group.items)


def test_already_crosslisted_blocks_whole_group(db):
    request = create_request(
        db,
        admin=admin(db),
        faculty_identity="faculty@example.edu",
        external_reference="TICKET-2",
        rows=ROWS,
    )
    service = ValidationService(get_settings(), canvas_fixture(already_crosslisted=True))
    assert not service.validate_request(db, request)
    group = request.groups[0]
    assert group.status == GroupStatus.BLOCKED.value
    assert all(item.status == ItemStatus.BLOCKED.value for item in group.items)
    assert "already cross-listed" in group.blocking_reason


def test_mixed_accounts_requires_destination(db):
    request = create_request(
        db,
        admin=admin(db),
        faculty_identity="faculty@example.edu",
        external_reference="TICKET-3",
        rows=ROWS,
    )
    canvas = canvas_fixture(mixed_accounts=True)
    service = ValidationService(get_settings(), canvas)
    assert not service.validate_request(db, request)
    group = request.groups[0]
    assert group.status == GroupStatus.NEEDS_DESTINATION.value

    set_destination_account(db, group=group, account_id=20, admin_id=request.admin_id)
    assert service.validate_group(db, group, admin_id=request.admin_id)
    assert group.destination_account_id == 20
    assert group.status == GroupStatus.READY.value


def test_live_validation_does_not_rewrite_completed_groups(db):
    request = create_request(
        db,
        admin=admin(db),
        faculty_identity="faculty@example.edu",
        external_reference="TICKET-4",
        rows=ROWS,
    )
    canvas = canvas_fixture()
    service = ValidationService(get_settings(), canvas)
    assert service.validate_request(db, request)
    request.groups[0].status = GroupStatus.COMPLETED.value
    request.groups[0].destination_course_id = 9001
    db.commit()

    canvas.sections["2026.fall.clj.101.12345"]["nonxlist_course_id"] = 201
    canvas.sections["2026.fall.clj.101.12345"]["course_id"] = 9001
    assert service.validate_request(db, request)
    assert request.groups[0].status == GroupStatus.COMPLETED.value
    assert request.status == RequestStatus.COMPLETED.value
