from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class RequestStatus(StrEnum):
    DRAFT = "draft"
    NEEDS_CORRECTION = "needs_correction"
    READY = "ready"
    QUEUED = "queued"
    EXECUTING = "executing"
    COMPLETED = "completed"
    COMPLETED_WITH_EXCEPTIONS = "completed_with_exceptions"


class GroupStatus(StrEnum):
    DRAFT = "draft"
    NEEDS_DESTINATION = "needs_destination"
    BLOCKED = "blocked"
    READY = "ready"
    QUEUED = "queued"
    EXECUTING = "executing"
    COMPLETED = "completed"
    COMPLETED_WITH_EXCEPTIONS = "completed_with_exceptions"
    CREATION_UNKNOWN = "creation_unknown"


class ItemStatus(StrEnum):
    PENDING = "pending"
    ELIGIBLE = "eligible"
    BLOCKED = "blocked"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_FINAL = "failed_final"
    STALE = "stale"


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AdminUser(Base):
    __tablename__ = "admin_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    canvas_user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(320))
    last_login_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    credential: Mapped[OAuthCredential] = relationship(
        back_populates="admin", cascade="all, delete-orphan", uselist=False
    )
    requests: Mapped[list[MergeRequest]] = relationship(back_populates="admin")


class OAuthCredential(Base):
    __tablename__ = "oauth_credential"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admin_user.id"), unique=True)
    encrypted_access_token: Mapped[str] = mapped_column(Text)
    encrypted_refresh_token: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scopes: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    admin: Mapped[AdminUser] = relationship(back_populates="credential")


class MergeRequest(Base):
    __tablename__ = "merge_request"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int] = mapped_column(ForeignKey("admin_user.id"), index=True)
    faculty_identity: Mapped[str] = mapped_column(String(320))
    external_reference: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(40), default=RequestStatus.DRAFT.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    admin: Mapped[AdminUser] = relationship(back_populates="requests")
    groups: Mapped[list[MergeGroup]] = relationship(
        back_populates="request", cascade="all, delete-orphan", order_by="MergeGroup.id"
    )


class MergeGroup(Base):
    __tablename__ = "merge_group"
    __table_args__ = (UniqueConstraint("request_id", "group_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(ForeignKey("merge_request.id"), index=True)
    group_key: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(50), default=GroupStatus.DRAFT.value)
    destination_account_id: Mapped[int | None] = mapped_column(Integer)
    destination_account_name: Mapped[str | None] = mapped_column(String(255))
    destination_course_id: Mapped[int | None] = mapped_column(Integer)
    destination_name: Mapped[str | None] = mapped_column(String(255))
    destination_course_code: Mapped[str | None] = mapped_column(String(255))
    enrollment_term_id: Mapped[int | None] = mapped_column(Integer)
    enrollment_term_name: Mapped[str | None] = mapped_column(String(255))
    validation_hash: Mapped[str | None] = mapped_column(String(64))
    confirmed_hash: Mapped[str | None] = mapped_column(String(64))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocking_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    request: Mapped[MergeRequest] = relationship(back_populates="groups")
    items: Mapped[list[MergeItem]] = relationship(
        back_populates="group", cascade="all, delete-orphan", order_by="MergeItem.id"
    )
    jobs: Mapped[list[ExecutionJob]] = relationship(back_populates="group")


class MergeItem(Base):
    __tablename__ = "merge_item"
    __table_args__ = (UniqueConstraint("group_id", "source_sis_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("merge_group.id"), index=True)
    source_sis_id: Mapped[str] = mapped_column(String(255))
    year: Mapped[int | None] = mapped_column(Integer)
    season: Mapped[str | None] = mapped_column(String(40))
    abbreviation: Mapped[str | None] = mapped_column(String(40))
    course_number: Mapped[str | None] = mapped_column(String(40))
    crn: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(50), default=ItemStatus.PENDING.value)
    reason: Mapped[str | None] = mapped_column(Text)
    canvas_section_id: Mapped[int | None] = mapped_column(Integer)
    canvas_source_course_id: Mapped[int | None] = mapped_column(Integer)
    source_account_id: Mapped[int | None] = mapped_column(Integer)
    source_account_name: Mapped[str | None] = mapped_column(String(255))
    nonxlist_course_id: Mapped[int | None] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    group: Mapped[MergeGroup] = relationship(back_populates="items")
    attempts: Mapped[list[ExecutionAttempt]] = relationship(
        back_populates="item", cascade="all, delete-orphan"
    )


class ExecutionJob(Base):
    __tablename__ = "execution_job"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("merge_group.id"), index=True)
    requested_by_admin_id: Mapped[int] = mapped_column(ForeignKey("admin_user.id"))
    status: Mapped[str] = mapped_column(String(40), default=JobStatus.PENDING.value, index=True)
    retry_failed_only: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    group: Mapped[MergeGroup] = relationship(back_populates="jobs")


class ExecutionAttempt(Base):
    __tablename__ = "execution_attempt"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int | None] = mapped_column(ForeignKey("merge_item.id"), index=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("merge_group.id"), index=True)
    action: Mapped[str] = mapped_column(String(60))
    outcome: Mapped[str] = mapped_column(String(60))
    http_status: Mapped[int | None] = mapped_column(Integer)
    canvas_request_id: Mapped[str | None] = mapped_column(String(255))
    detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    item: Mapped[MergeItem | None] = relationship(back_populates="attempts")


class AuditEvent(Base):
    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    admin_id: Mapped[int | None] = mapped_column(ForeignKey("admin_user.id"), index=True)
    request_id: Mapped[int | None] = mapped_column(ForeignKey("merge_request.id"), index=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("merge_group.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    admin: Mapped[AdminUser | None] = relationship()
    request: Mapped[MergeRequest | None] = relationship()
