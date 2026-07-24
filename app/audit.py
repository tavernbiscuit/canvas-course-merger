from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent


def record_event(
    db: Session,
    event_type: str,
    *,
    admin_id: int | None = None,
    request_id: int | None = None,
    group_id: int | None = None,
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        event_type=event_type,
        admin_id=admin_id,
        request_id=request_id,
        group_id=group_id,
        details=details or {},
    )
    db.add(event)
    return event
