from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    ExecutionJob,
    GroupStatus,
    ItemStatus,
    JobStatus,
    MergeGroup,
    MergeRequest,
)

STEP_KEYS = ("queued", "preflight", "destination", "sections", "complete")
STEP_LABELS = {
    "queued": "Queued",
    "preflight": "Live safety check",
    "destination": "Create destination",
    "sections": "Cross-list sections",
    "complete": "Complete",
}
SUCCESSFUL_ITEM_STATUSES = {ItemStatus.SUCCEEDED.value}
FAILED_ITEM_STATUSES = {
    ItemStatus.FAILED_RETRYABLE.value,
    ItemStatus.FAILED_FINAL.value,
    ItemStatus.STALE.value,
}
RESOLVED_ITEM_STATUSES = SUCCESSFUL_ITEM_STATUSES | FAILED_ITEM_STATUSES


def _latest_job(group: MergeGroup) -> ExecutionJob | None:
    return max(group.jobs, key=lambda job: job.id, default=None)


def _step_states(
    current_step: str,
    *,
    terminal_success: bool = False,
    terminal_error: bool = False,
) -> list[dict[str, str]]:
    current_index = STEP_KEYS.index(current_step)
    steps: list[dict[str, str]] = []
    for index, key in enumerate(STEP_KEYS):
        if terminal_success:
            state = "complete"
        elif index < current_index:
            state = "complete"
        elif index == current_index:
            state = "error" if terminal_error else "active"
        else:
            state = "waiting"
        steps.append({"key": key, "label": STEP_LABELS[key], "state": state})
    return steps


def _group_progress(
    group: MergeGroup,
    job: ExecutionJob,
    queue_positions: dict[int, int],
) -> dict[str, Any]:
    total = len(group.items)
    succeeded = sum(item.status in SUCCESSFUL_ITEM_STATUSES for item in group.items)
    failed = sum(item.status in FAILED_ITEM_STATUSES for item in group.items)
    resolved = sum(item.status in RESOLVED_ITEM_STATUSES for item in group.items)
    destination_created = group.destination_course_id is not None
    terminal = job.status in {JobStatus.SUCCEEDED.value, JobStatus.FAILED.value}

    if job.status == JobStatus.PENDING.value:
        position = queue_positions.get(job.id)
        title = "Queued for execution"
        detail = (
            f"Position {position} in the execution queue. "
            "The worker will start this destination automatically."
            if position
            else "Waiting for the execution worker to claim this destination."
        )
        current_step = "queued"
        percent = 5
        tone = "active"
    elif job.status == JobStatus.RUNNING.value and not destination_created:
        if group.status == GroupStatus.CREATION_UNKNOWN.value:
            title = "Creating the blank destination course"
            detail = (
                "Canvas is processing the new unpublished course. "
                "The application will save its Canvas ID before moving any sections."
            )
            current_step = "destination"
            percent = 32
        else:
            title = "Rechecking live Canvas state"
            detail = (
                "Permissions, section eligibility, and destination metadata are being "
                "verified again immediately before creation."
            )
            current_step = "preflight"
            percent = 16
        tone = "active"
    elif job.status == JobStatus.RUNNING.value:
        title = "Cross-listing sections"
        detail = f"{resolved} of {total} sections processed."
        current_step = "sections"
        percent = 45 + round((resolved / max(total, 1)) * 50)
        tone = "active"
    elif job.status == JobStatus.SUCCEEDED.value and group.status == GroupStatus.COMPLETED.value:
        title = "Merge completed"
        detail = f"All {total} sections were cross-listed successfully."
        current_step = "complete"
        percent = 100
        tone = "success"
    elif job.status == JobStatus.SUCCEEDED.value:
        title = "Completed with exceptions"
        detail = (
            f"{succeeded} of {total} sections were cross-listed. "
            f"{failed} require review or retry."
        )
        current_step = "complete"
        percent = 100
        tone = "warning"
    else:
        if group.status == GroupStatus.CREATION_UNKNOWN.value:
            title = "Administrator reconciliation required"
            detail = (
                job.error
                or "Canvas may have created the destination, but its response was not received."
            )
            current_step = "destination"
        elif destination_created:
            title = "Execution stopped"
            detail = job.error or group.blocking_reason or "Section processing could not continue."
            current_step = "sections"
        else:
            title = "Execution stopped before course creation"
            detail = job.error or group.blocking_reason or "The final safety check did not pass."
            current_step = "preflight"
        percent = 100
        tone = "error"

    terminal_success = (
        job.status == JobStatus.SUCCEEDED.value
        and group.status == GroupStatus.COMPLETED.value
    )
    terminal_error = job.status == JobStatus.FAILED.value
    steps = _step_states(
        current_step,
        terminal_success=terminal_success,
        terminal_error=terminal_error,
    )
    if job.status == JobStatus.SUCCEEDED.value and not terminal_success:
        steps = _step_states("complete")
        steps[-1]["state"] = "warning"

    return {
        "group_id": group.id,
        "group_status": group.status,
        "display_status": (
            GroupStatus.EXECUTING.value
            if job.status == JobStatus.RUNNING.value
            else group.status
        ),
        "job_id": job.id,
        "job_status": job.status,
        "retry": job.retry_failed_only,
        "queue_position": queue_positions.get(job.id),
        "title": title,
        "detail": detail,
        "tone": tone,
        "percent": min(percent, 100),
        "terminal": terminal,
        "destination_course_id": group.destination_course_id,
        "counts": {
            "total": total,
            "succeeded": succeeded,
            "failed": failed,
            "resolved": resolved,
        },
        "steps": steps,
        "items": [
            {
                "id": item.id,
                "source_sis_id": item.source_sis_id,
                "status": item.status,
                "reason": item.reason,
            }
            for item in group.items
        ],
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "updated_at": (
            job.finished_at or job.heartbeat_at or job.created_at
        ).isoformat(),
    }


def load_request_progress(db: Session, request_id: int) -> dict[str, Any] | None:
    merge_request = db.scalar(
        select(MergeRequest)
        .where(MergeRequest.id == request_id)
        .options(
            selectinload(MergeRequest.groups).selectinload(MergeGroup.items),
            selectinload(MergeRequest.groups).selectinload(MergeGroup.jobs),
        )
    )
    if not merge_request:
        return None

    pending_job_ids = db.scalars(
        select(ExecutionJob.id)
        .where(ExecutionJob.status == JobStatus.PENDING.value)
        .order_by(ExecutionJob.created_at, ExecutionJob.id)
    ).all()
    queue_positions = {job_id: index for index, job_id in enumerate(pending_job_ids, start=1)}

    groups = []
    for group in merge_request.groups:
        latest_job = _latest_job(group)
        if latest_job:
            groups.append(_group_progress(group, latest_job, queue_positions))

    return {
        "request_id": merge_request.id,
        "request_status": merge_request.status,
        "active": any(not group["terminal"] for group in groups),
        "poll_after_ms": 1500 if any(not group["terminal"] for group in groups) else None,
        "groups": groups,
    }
