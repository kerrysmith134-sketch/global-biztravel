"""签证案件与入境评估模型。对应迁移 ``0018_visa_cases``。

PRD「方向二：AI 签证与跨境出行管家」要求把一次提醒升级为工作流：

出出行计划 → 入境要求评估 → 材料清单 → 申请进度 → 出发前检查。

两张表刻意分开：

- ``entry_assessments`` 是**外部要求快照**（来源、版本、置信度、是否需人工复核），可多次刷新；
- ``visa_cases`` 是**用户自己的办理进度**（国家、状态、清单、提交/决定时间）。

``visa_cases`` 不存护照号；只允许可选的 ``passport_expires_at`` 用于时效检查（PII 最小化）。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk

VISA_CASE_STATUSES = ("NEW", "PREPARING", "SUBMITTED", "APPROVED", "REJECTED", "CANCELLED")


class VisaCase(Base):
    __tablename__ = "visa_cases"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "trip_id", "country_code", name="uq_visa_cases_org_trip_country"
        ),
        CheckConstraint(
            "status IN ('NEW','PREPARING','SUBMITTED','APPROVED','REJECTED','CANCELLED')",
            name="status_allowed",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        Index("ix_visa_cases_org_status", "organization_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    country_code: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="NEW")
    checklist: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    notes: Mapped[str | None] = mapped_column(Text)
    passport_expires_at: Mapped[date | None] = mapped_column(Date)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.6")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class EntryAssessment(Base):
    __tablename__ = "entry_assessments"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "trip_id",
            "country_code",
            "source_version",
            name="uq_entry_assessments_org_trip_country_version",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        Index("ix_entry_assessments_org_trip", "organization_id", "trip_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    country_code: Mapped[str] = mapped_column(CHAR(2), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    source_version: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.6")
    manual_review_required: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    assessed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at()


__all__ = ["EntryAssessment", "VISA_CASE_STATUSES", "VisaCase"]
