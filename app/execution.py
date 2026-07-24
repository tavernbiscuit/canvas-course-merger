from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.audit import record_event
from app.canvas import CanvasClient, CanvasError, OAuthService
from app.config import Settings
from app.models import (
    AdminUser,
    ExecutionAttempt,
    ExecutionJob,
    GroupStatus,
    ItemStatus,
    JobStatus,
    MergeGroup,
    MergeItem,
    RequestStatus,
    utcnow,
)
from app.workflow import ValidationService, refresh_request_status


def queue_group(
    db: Session,
    *,
    group: MergeGroup,
    admin: AdminUser,
    retry_failed_only: bool = False,
) -> ExecutionJob:
    if group.status in (GroupStatus.QUEUED.value, GroupStatus.EXECUTING.value):
        raise ValueError("This destination course group already has an active execution job")
    if retry_failed_only:
        if group.destination_course_id is None:
            raise ValueError("A retry requires an existing destination course")
        if not any(
            item.status in (ItemStatus.FAILED_RETRYABLE.value, ItemStatus.FAILED_FINAL.value)
            for item in group.items
        ):
            raise ValueError("This destination course group has no failed Canvas sections to retry")
    else:
        if group.status != GroupStatus.READY.value:
            raise ValueError("Only a fully validated destination course group can be executed")
        if not group.validation_hash:
            raise ValueError("The validation snapshot is missing")
        group.confirmed_hash = group.validation_hash
        group.confirmed_at = utcnow()
    job = ExecutionJob(
        group_id=group.id,
        requested_by_admin_id=admin.id,
        retry_failed_only=retry_failed_only,
    )
    db.add(job)
    group.status = GroupStatus.QUEUED.value
    group.request.status = RequestStatus.QUEUED.value
    record_event(
        db,
        "group.execution_queued",
        admin_id=admin.id,
        request_id=group.request_id,
        group_id=group.id,
        details={
            "validation_hash": group.confirmed_hash,
            "retry_failed_only": retry_failed_only,
        },
    )
    db.commit()
    return job


def claim_next_job(db: Session) -> ExecutionJob | None:
    job = db.scalar(
        select(ExecutionJob)
        .where(ExecutionJob.status == JobStatus.PENDING.value)
        .order_by(ExecutionJob.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if not job:
        return None
    job.status = JobStatus.RUNNING.value
    job.started_at = utcnow()
    job.heartbeat_at = utcnow()
    db.commit()
    return job


def recover_stale_jobs(db: Session, *, stale_after: timedelta = timedelta(minutes=30)) -> int:
    cutoff = utcnow() - stale_after
    jobs = db.scalars(
        select(ExecutionJob)
        .where(ExecutionJob.status == JobStatus.RUNNING.value)
        .where(ExecutionJob.heartbeat_at < cutoff)
        .options(selectinload(ExecutionJob.group))
    ).all()
    recovered = 0
    for job in jobs:
        if job.group.status == GroupStatus.CREATION_UNKNOWN.value:
            job.status = JobStatus.FAILED.value
            job.error = (
                "Worker stopped while destination creation was in flight; "
                "administrator reconciliation is required"
            )
            job.finished_at = utcnow()
        else:
            job.status = JobStatus.PENDING.value
            job.started_at = None
            job.heartbeat_at = None
            job.group.status = GroupStatus.QUEUED.value
            recovered += 1
    if jobs:
        db.commit()
    return recovered


class ExecutionService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.oauth = OAuthService(settings)

    def _load(self, db: Session, job_id: int) -> tuple[ExecutionJob, MergeGroup, AdminUser]:
        job = db.scalar(
            select(ExecutionJob)
            .where(ExecutionJob.id == job_id)
            .options(
                selectinload(ExecutionJob.group)
                .selectinload(MergeGroup.items)
                .selectinload(MergeItem.attempts),
                selectinload(ExecutionJob.group).selectinload(MergeGroup.request),
            )
        )
        if not job:
            raise RuntimeError(f"Execution job {job_id} no longer exists")
        admin = db.scalar(
            select(AdminUser)
            .where(AdminUser.id == job.requested_by_admin_id)
            .options(selectinload(AdminUser.credential))
        )
        if not admin or not admin.credential:
            raise RuntimeError("The executing admin has no Canvas OAuth credential")
        return job, job.group, admin

    @staticmethod
    def _attempt(
        db: Session,
        group: MergeGroup,
        action: str,
        outcome: str,
        *,
        item: MergeItem | None = None,
        error: CanvasError | None = None,
        request_id: str | None = None,
    ) -> None:
        db.add(
            ExecutionAttempt(
                group_id=group.id,
                item_id=item.id if item else None,
                action=action,
                outcome=outcome,
                http_status=error.status_code if error else None,
                canvas_request_id=request_id or (error.request_id if error else None),
                detail=str(error) if error else None,
            )
        )

    def _check_retry_state(self, canvas: CanvasClient, group: MergeGroup, item: MergeItem) -> str:
        section = canvas.get_section_by_sis_id(item.source_sis_id)
        current_course_id = int(section["course_id"])
        if current_course_id == group.destination_course_id:
            return "already_succeeded"
        if section.get("nonxlist_course_id") is not None:
            raise CanvasError("Section is now cross-listed to another course")
        if current_course_id != item.canvas_source_course_id:
            raise CanvasError("Section source course changed after validation")
        return "eligible"

    def run_job(self, db: Session, job_id: int) -> None:
        job, group, admin = self._load(db, job_id)
        group.status = GroupStatus.EXECUTING.value
        group.request.status = RequestStatus.EXECUTING.value
        job.heartbeat_at = utcnow()
        db.commit()

        try:
            token = self.oauth.access_token(admin.credential, db)
            with CanvasClient(self.settings, token) as canvas:
                destination_id = group.destination_course_id
                resuming_existing_destination = destination_id is not None
                if not job.retry_failed_only and not resuming_existing_destination:
                    confirmed_hash = group.confirmed_hash
                    validator = ValidationService(self.settings, canvas)
                    validator.validate_group(db, group, admin_id=admin.id)
                    if group.status != GroupStatus.READY.value:
                        raise RuntimeError(
                            f"Pre-execution validation failed: {group.blocking_reason}"
                        )
                    if group.validation_hash != confirmed_hash:
                        for item in group.items:
                            if item.status == ItemStatus.ELIGIBLE.value:
                                item.status = ItemStatus.STALE.value
                        raise RuntimeError(
                            "Canvas state or destination metadata changed after confirmation"
                        )

                if destination_id is None:
                    # Persist an uncertainty marker before the side effect. If this
                    # process dies after Canvas accepts the request but before the
                    # response is stored, recovery must never create a duplicate.
                    group.status = GroupStatus.CREATION_UNKNOWN.value
                    job.heartbeat_at = utcnow()
                    record_event(
                        db,
                        "group.destination_creation_started",
                        admin_id=admin.id,
                        request_id=group.request_id,
                        group_id=group.id,
                        details={
                            "destination_account_id": group.destination_account_id,
                            "destination_name": group.destination_name,
                        },
                    )
                    db.commit()
                    try:
                        response = canvas.create_course(
                            group.destination_account_id or 0,
                            name=group.destination_name or "",
                            course_code=group.destination_course_code or "",
                            term_id=group.enrollment_term_id or 0,
                        )
                    except CanvasError as exc:
                        self._attempt(
                            db,
                            group,
                            "create_course",
                            "creation_unknown" if exc.ambiguous else "failed",
                            error=exc,
                        )
                        if exc.ambiguous or exc.retryable:
                            group.status = GroupStatus.CREATION_UNKNOWN.value
                        else:
                            group.status = GroupStatus.BLOCKED.value
                        raise
                    destination_id = int(response.data["id"])
                    group.destination_course_id = destination_id
                    group.status = GroupStatus.EXECUTING.value
                    job.heartbeat_at = utcnow()
                    self._attempt(
                        db,
                        group,
                        "create_course",
                        "succeeded",
                        request_id=response.request_id,
                    )
                    record_event(
                        db,
                        "group.destination_created",
                        admin_id=admin.id,
                        request_id=group.request_id,
                        group_id=group.id,
                        details={"destination_course_id": destination_id},
                    )
                    # This commit is an intentional recovery boundary.
                    db.commit()

                candidates = [
                    item
                    for item in group.items
                    if item.status
                    in (
                        ItemStatus.ELIGIBLE.value,
                        ItemStatus.FAILED_RETRYABLE.value,
                        ItemStatus.FAILED_FINAL.value,
                    )
                ]
                for item in candidates:
                    # Re-fetch immediately before every mutation. This both closes
                    # the validation/execution race and safely reconciles a prior
                    # attempt whose response was lost.
                    try:
                        if self._check_retry_state(canvas, group, item) == "already_succeeded":
                            item.status = ItemStatus.SUCCEEDED.value
                            item.reason = None
                            self._attempt(db, group, "crosslist", "reconciled", item=item)
                            db.commit()
                            continue
                    except CanvasError as exc:
                        item.status = ItemStatus.FAILED_FINAL.value
                        item.reason = str(exc)
                        self._attempt(db, group, "crosslist", "blocked", item=item, error=exc)
                        db.commit()
                        continue
                    try:
                        response = canvas.crosslist_section(item.source_sis_id, destination_id)
                        item.status = ItemStatus.SUCCEEDED.value
                        item.reason = None
                        self._attempt(
                            db,
                            group,
                            "crosslist",
                            "succeeded",
                            item=item,
                            request_id=response.request_id,
                        )
                    except CanvasError as exc:
                        item.status = (
                            ItemStatus.FAILED_RETRYABLE.value
                            if exc.retryable or exc.ambiguous
                            else ItemStatus.FAILED_FINAL.value
                        )
                        item.reason = str(exc)
                        self._attempt(db, group, "crosslist", "failed", item=item, error=exc)
                    job.heartbeat_at = utcnow()
                    db.commit()

                failures = [
                    item
                    for item in group.items
                    if item.status
                    in (ItemStatus.FAILED_RETRYABLE.value, ItemStatus.FAILED_FINAL.value)
                ]
                group.status = (
                    GroupStatus.COMPLETED_WITH_EXCEPTIONS.value
                    if failures
                    else GroupStatus.COMPLETED.value
                )
                job.status = JobStatus.SUCCEEDED.value
                job.finished_at = utcnow()
                refresh_request_status(group.request)
                record_event(
                    db,
                    "group.execution_finished",
                    admin_id=admin.id,
                    request_id=group.request_id,
                    group_id=group.id,
                    details={
                        "status": group.status,
                        "destination_course_id": destination_id,
                        "failed_sections": len(failures),
                    },
                )
                db.commit()
        except Exception as exc:
            job.error = str(exc)
            job.status = JobStatus.FAILED.value
            job.finished_at = utcnow()
            if group.status not in (
                GroupStatus.CREATION_UNKNOWN.value,
                GroupStatus.BLOCKED.value,
            ):
                group.status = GroupStatus.BLOCKED.value
                group.blocking_reason = str(exc)
            refresh_request_status(group.request)
            record_event(
                db,
                "group.execution_failed",
                admin_id=admin.id,
                request_id=group.request_id,
                group_id=group.id,
                details={"error": str(exc), "status": group.status},
            )
            db.commit()
