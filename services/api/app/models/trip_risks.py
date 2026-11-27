"""Trip-level risk cases and append-only recovery lifecycle events.

This aggregate joins independently owned sources (ProactiveSuggestion, VisitFollowUp,
ExternalSignal and plan/flight evidence) without moving or duplicating their source records.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk

TRIP_RISK_STATUSES = (
    "OPEN",
    "ACKNOWLEDGED",
    "IN_PROGRESS",
    "WAITING_EXTERNAL",
    "ESCALATED",
    "RESOLVED",
    "DISMISSED",
)
TRIP_RISK_SEVERITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
TRIP_RISK_EVENT_TYPES = (
    "OPENED",
    "OBSERVED",
    "SOURCE_LINKED",
    "ACKNOWLEDGED",
    "STARTED",
    "WAITING_EXTERNAL",
    "ESCALATED",
    "RESOLVED",
    "DISMISSED",
    "REOPENED",
    "REVIEWED",
)


class TripRiskCase(Base):
    """One operational risk episode for a Trip; source systems remain authoritative."""

    __tablename__ = "trip_risk_cases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "trip_id"],
            ["trips.organization_id", "trips.id"],
            ondelete="CASCADE",
            name="fk_trip_risk_cases_trip_same_org",
        ),
        UniqueConstraint(
            "organization_id", "trip_id", "id",
            name="uq_trip_risk_cases_org_trip_id",
        ),
        UniqueConstraint(
            "organization_id", "trip_id", "case_key",
            name="uq_trip_risk_cases_org_trip_case_key",
        ),
        CheckConstraint(
            "status IN ('OPEN','ACKNOWLEDGED','IN_PROGRESS','WAITING_EXTERNAL','ESCALATED','RESOLVED','DISMISSED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "severity IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name="severity_allowed",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("length(btrim(case_key)) > 0", name="case_key_not_blank"),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        Index(
            "ix_trip_risk_cases_org_trip_status_review",
            "organization_id",
            "trip_id",
            "status",
            "next_review_at",
        ),
        Index(
            "ix_trip_risk_cases_org_status_review",
            "organization_id",
            "status",
            "next_review_at",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    case_key: Mapped[str] = mapped_column(Text, nullable=False)
    risk_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="OPEN")
    severity: Mapped[str] = mapped_column(Text, nullable=False, server_default="MEDIUM")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    source_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    evidence_refs: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    context: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    next_review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    waiting_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_basis: Mapped[str | None] = mapped_column(Text)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class TripRiskCaseEvent(Base):
    """Append-only actor, source and state-transition audit for a TripRiskCase."""

    __tablename__ = "trip_risk_case_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "trip_id", "case_id"],
            ["trip_risk_cases.organization_id", "trip_risk_cases.trip_id", "trip_risk_cases.id"],
            ondelete="CASCADE",
            name="fk_trip_risk_case_events_case_same_trip",
        ),
        CheckConstraint(
            "event_type IN ('OPENED','OBSERVED','SOURCE_LINKED','ACKNOWLEDGED','STARTED',"
            "'WAITING_EXTERNAL','ESCALATED','RESOLVED','DISMISSED','REOPENED','REVIEWED')",
            name="event_type_allowed",
        ),
        CheckConstraint("actor_type IN ('SYSTEM','USER')", name="actor_type_allowed"),
        Index(
            "ix_trip_risk_case_events_org_trip_case_created",
            "organization_id",
            "trip_id",
            "case_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    case_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str] = mapped_column(Text, nullable=False)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="SYSTEM")
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = created_at()


__all__ = [
    "TRIP_RISK_EVENT_TYPES",
    "TRIP_RISK_SEVERITIES",
    "TRIP_RISK_STATUSES",
    "TripRiskCase",
    "TripRiskCaseEvent",
]
