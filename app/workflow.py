from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.audit import record_event
from app.canvas import CanvasClient, CanvasError
from app.config import Settings
from app.domain import (
    DomainValidationError,
    ParsedSisId,
    canonical_hash,
    destination_metadata,
    parse_sis_id,
)
from app.intake import IntakeRow
from app.models import (
    AdminUser,
    GroupStatus,
    ItemStatus,
    MergeGroup,
    MergeItem,
    MergeRequest,
    RequestStatus,
    utcnow,
)

REQUIRED_PERMISSIONS = ("manage_courses", "manage_sections", "read_sis")
TERMINAL_GROUP_STATUSES = {
    GroupStatus.COMPLETED.value,
    GroupStatus.COMPLETED_WITH_EXCEPTIONS.value,
    GroupStatus.CREATION_UNKNOWN.value,
}


def refresh_request_status(request: MergeRequest) -> None:
    statuses = {group.status for group in request.groups}
    if statuses == {GroupStatus.COMPLETED.value}:
        request.status = RequestStatus.COMPLETED.value
    elif GroupStatus.EXECUTING.value in statuses:
        request.status = RequestStatus.EXECUTING.value
    elif GroupStatus.QUEUED.value in statuses:
        request.status = RequestStatus.QUEUED.value
    elif statuses & {GroupStatus.BLOCKED.value, GroupStatus.NEEDS_DESTINATION.value}:
        request.status = RequestStatus.NEEDS_CORRECTION.value
    elif GroupStatus.READY.value in statuses:
        request.status = RequestStatus.READY.value
    elif statuses and statuses <= TERMINAL_GROUP_STATUSES:
        request.status = RequestStatus.COMPLETED_WITH_EXCEPTIONS.value
    else:
        request.status = RequestStatus.DRAFT.value


def create_request(
    db: Session,
    *,
    admin: AdminUser,
    faculty_identity: str,
    external_reference: str,
    rows: list[IntakeRow],
) -> MergeRequest:
    request = MergeRequest(
        admin=admin,
        faculty_identity=faculty_identity.strip(),
        external_reference=external_reference.strip(),
    )
    if not request.faculty_identity or not request.external_reference:
        raise DomainValidationError("Faculty identity and external request reference are required")
    db.add(request)
    grouped: dict[str, list[IntakeRow]] = defaultdict(list)
    for row in rows:
        grouped[row.merge_group_key.strip()].append(row)
    for group_key, group_rows in grouped.items():
        requested_accounts = {
            row.destination_subaccount
            for row in group_rows
            if row.destination_subaccount is not None
        }
        group = MergeGroup(group_key=group_key)
        if len(requested_accounts) == 1:
            group.destination_account_id = next(iter(requested_accounts))
        elif len(requested_accounts) > 1:
            group.status = GroupStatus.BLOCKED.value
            group.blocking_reason = (
                "A destination course group may specify only one Canvas destination subaccount"
            )
        request.groups.append(group)
        seen: set[str] = set()
        for row in group_rows:
            normalized = row.source_sis_id.strip().lower()
            if normalized in seen:
                raise DomainValidationError(
                    f"Duplicate source SIS section ID {row.source_sis_id} "
                    f"in destination course group {group_key}"
                )
            seen.add(normalized)
            group.items.append(MergeItem(source_sis_id=row.source_sis_id.strip()))
    db.flush()
    record_event(
        db,
        "request.created",
        admin_id=admin.id,
        request_id=request.id,
        details={
            "faculty_identity": request.faculty_identity,
            "external_reference": request.external_reference,
            "groups": len(request.groups),
            "sections": sum(len(group.items) for group in request.groups),
        },
    )
    db.commit()
    return request


def load_request(db: Session, request_id: int) -> MergeRequest | None:
    return db.scalar(
        select(MergeRequest)
        .where(MergeRequest.id == request_id)
        .options(
            selectinload(MergeRequest.groups).selectinload(MergeGroup.items),
            selectinload(MergeRequest.groups).selectinload(MergeGroup.jobs),
            selectinload(MergeRequest.admin),
        )
    )


class ValidationService:
    def __init__(self, settings: Settings, canvas: CanvasClient):
        self.settings = settings
        self.canvas = canvas

    @staticmethod
    def _root_id(account: dict[str, Any]) -> int:
        return int(account.get("root_account_id") or account["id"])

    def allowed_accounts(self) -> dict[int, dict[str, Any]]:
        accounts = {int(account["id"]): account for account in self.canvas.manageable_accounts()}
        if self.settings.canvas_allowed_account_ids:
            accounts = {
                account_id: account
                for account_id, account in accounts.items()
                if account_id in self.settings.canvas_allowed_account_ids
            }
        return accounts

    def validate_group(
        self,
        db: Session,
        group: MergeGroup,
        *,
        admin_id: int,
    ) -> bool:
        group.blocking_reason = None
        group.confirmed_hash = None
        group.confirmed_at = None
        parsed_items: list[tuple[MergeItem, ParsedSisId]] = []
        blocking_reasons: list[str] = []
        now = utcnow()

        for item in group.items:
            item.reason = None
            item.status = ItemStatus.PENDING.value
            try:
                parsed = parse_sis_id(item.source_sis_id)
                item.source_sis_id = parsed.normalized
                item.year = parsed.year
                item.season = parsed.season
                item.abbreviation = parsed.abbreviation
                item.course_number = parsed.course_number
                item.crn = parsed.crn
                parsed_items.append((item, parsed))
            except DomainValidationError as exc:
                item.status = ItemStatus.BLOCKED.value
                item.reason = str(exc)
                blocking_reasons.append(f"{item.source_sis_id}: {exc}")

        if len(group.items) < 2:
            blocking_reasons.append(
                "A destination course group requires at least two distinct Canvas sections"
            )

        allowed_accounts: dict[int, dict[str, Any]] = {}
        try:
            allowed_accounts = self.allowed_accounts()
        except CanvasError as exc:
            blocking_reasons.append(f"Unable to determine manageable Canvas accounts: {exc}")

        years = {parsed.year for _, parsed in parsed_items}
        seasons = {parsed.season for _, parsed in parsed_items}
        if len(years) > 1 or len(seasons) > 1:
            blocking_reasons.append("All sections must share the same SIS year and season")

        term_ids: set[int] = set()
        term_names: set[str] = set()
        source_account_ids: set[int] = set()
        source_root_ids: set[int] = set()

        for item, parsed in parsed_items:
            try:
                section = self.canvas.get_section_by_sis_id(parsed.normalized)
                item.canvas_section_id = int(section["id"])
                item.canvas_source_course_id = int(section["course_id"])
                item.nonxlist_course_id = section.get("nonxlist_course_id")
                if section.get("nonxlist_course_id") is not None:
                    raise DomainValidationError("Section is already cross-listed")
                if section.get("sis_section_id") != parsed.normalized:
                    raise DomainValidationError("Canvas returned a different SIS section ID")
                if section.get("sis_course_id") != parsed.normalized:
                    raise DomainValidationError(
                        "The original SIS course ID does not match the SIS section ID"
                    )

                course = self.canvas.get_course(int(section["course_id"]))
                if course.get("sis_course_id") != parsed.normalized:
                    raise DomainValidationError(
                        "The source Canvas course has a different SIS course ID"
                    )
                term = course.get("term") or {}
                if not term.get("id"):
                    raise DomainValidationError("The source course has no Canvas enrollment term")
                account_id = int(course["account_id"])
                account = self.canvas.get_account(account_id)
                root_id = self._root_id(account)
                if root_id != self.settings.canvas_root_account_id:
                    raise DomainValidationError(
                        "Source course is outside the configured root account"
                    )

                item.source_account_id = account_id
                item.source_account_name = account.get("name")
                term_ids.add(int(term["id"]))
                term_names.add(str(term.get("name") or term["id"]))
                source_account_ids.add(account_id)
                source_root_ids.add(root_id)
                item.snapshot = {
                    "section_id": int(section["id"]),
                    "sis_section_id": section.get("sis_section_id"),
                    "course_id": int(section["course_id"]),
                    "sis_course_id": course.get("sis_course_id"),
                    "nonxlist_course_id": section.get("nonxlist_course_id"),
                    "term_id": int(term["id"]),
                    "account_id": account_id,
                    "root_account_id": root_id,
                }
                item.status = ItemStatus.ELIGIBLE.value
                item.validated_at = now
            except (CanvasError, DomainValidationError, KeyError, TypeError, ValueError) as exc:
                item.status = ItemStatus.BLOCKED.value
                item.reason = str(exc)
                blocking_reasons.append(f"{parsed.normalized}: {exc}")

        if len(term_ids) > 1:
            blocking_reasons.append("All sections must share one Canvas enrollment term")
        if len(source_root_ids) > 1:
            blocking_reasons.append("All sections must belong to one Canvas root account")
        for source_account_id in sorted(source_account_ids):
            try:
                source_permissions = self.canvas.account_permissions(source_account_id)
                missing = [
                    name for name in REQUIRED_PERMISSIONS if not source_permissions.get(name)
                ]
                if missing:
                    blocking_reasons.append(
                        f"Missing permissions in source account {source_account_id}: "
                        f"{', '.join(missing)}"
                    )
            except CanvasError as exc:
                blocking_reasons.append(
                    f"Unable to validate source account {source_account_id}: {exc}"
                )

        destination_account_id = group.destination_account_id
        if destination_account_id is None and len(source_account_ids) == 1:
            destination_account_id = next(iter(source_account_ids))
            group.destination_account_id = destination_account_id
        if destination_account_id is None and len(source_account_ids) > 1:
            group.status = GroupStatus.NEEDS_DESTINATION.value
            blocking_reasons.append(
                "Choose an approved destination subaccount for this cross-subaccount merge"
            )
        destination_account = allowed_accounts.get(destination_account_id or -1)
        if destination_account_id is not None and destination_account is None:
            blocking_reasons.append(
                "The selected destination subaccount is not approved or manageable by this admin"
            )
        if destination_account:
            try:
                account = self.canvas.get_account(destination_account_id)  # type: ignore[arg-type]
                if self._root_id(account) != self.settings.canvas_root_account_id:
                    blocking_reasons.append(
                        "The destination is outside the configured Canvas root account"
                    )
                permissions = self.canvas.account_permissions(destination_account_id)  # type: ignore[arg-type]
                missing = [name for name in REQUIRED_PERMISSIONS if not permissions.get(name)]
                if missing:
                    blocking_reasons.append(
                        f"Missing destination permissions: {', '.join(missing)}"
                    )
                group.destination_account_name = account.get("name")
            except CanvasError as exc:
                blocking_reasons.append(f"Unable to validate destination subaccount: {exc}")

        if parsed_items:
            name, code = destination_metadata(parsed for _, parsed in parsed_items)
            group.destination_name = name
            group.destination_course_code = code
        if len(term_ids) == 1:
            group.enrollment_term_id = next(iter(term_ids))
            group.enrollment_term_name = next(iter(term_names))

        snapshot_payload = {
            "group_key": group.group_key,
            "destination_account_id": group.destination_account_id,
            "destination_name": group.destination_name,
            "destination_course_code": group.destination_course_code,
            "term_id": group.enrollment_term_id,
            "items": [item.snapshot for item in group.items],
        }
        group.validation_hash = canonical_hash(snapshot_payload)
        if blocking_reasons:
            if group.status != GroupStatus.NEEDS_DESTINATION.value:
                group.status = GroupStatus.BLOCKED.value
            group.blocking_reason = "; ".join(dict.fromkeys(blocking_reasons))
            for item in group.items:
                if item.status == ItemStatus.ELIGIBLE.value:
                    item.status = ItemStatus.BLOCKED.value
                    if group.status == GroupStatus.NEEDS_DESTINATION.value:
                        item.reason = "Waiting for an approved destination subaccount selection"
                    else:
                        item.reason = (
                            "The entire destination course group is blocked because "
                            "another section is ineligible"
                        )
        else:
            group.status = GroupStatus.READY.value

        record_event(
            db,
            "group.validated",
            admin_id=admin_id,
            request_id=group.request_id,
            group_id=group.id,
            details={
                "status": group.status,
                "validation_hash": group.validation_hash,
                "blocking_reason": group.blocking_reason,
            },
        )
        db.flush()
        return not blocking_reasons

    def validate_request(self, db: Session, request: MergeRequest) -> bool:
        results = []
        for group in request.groups:
            if group.status in TERMINAL_GROUP_STATUSES | {
                GroupStatus.QUEUED.value,
                GroupStatus.EXECUTING.value,
            }:
                continue
            results.append(self.validate_group(db, group, admin_id=request.admin_id))
        refresh_request_status(request)
        db.commit()
        return all(results) and request.status not in {
            RequestStatus.NEEDS_CORRECTION.value,
            RequestStatus.DRAFT.value,
        }


def set_destination_account(
    db: Session,
    *,
    group: MergeGroup,
    account_id: int,
    admin_id: int,
) -> None:
    if group.destination_course_id is not None or group.status in TERMINAL_GROUP_STATUSES | {
        GroupStatus.QUEUED.value,
        GroupStatus.EXECUTING.value,
    }:
        raise ValueError("The destination can no longer be changed for this group")
    group.destination_account_id = account_id
    group.destination_account_name = None
    group.validation_hash = None
    group.confirmed_hash = None
    group.confirmed_at = None
    group.status = GroupStatus.DRAFT.value
    record_event(
        db,
        "group.destination_selected",
        admin_id=admin_id,
        request_id=group.request_id,
        group_id=group.id,
        details={"destination_account_id": account_id},
    )
    db.commit()
