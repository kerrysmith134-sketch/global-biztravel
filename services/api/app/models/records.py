"""现场拜访记录模型。对应迁移 ``0006_visit_records``。

设计文档 v2.0 第 10.5 章把拜访记录的离线冲突策略定为 **last-writer-wins + 保留冲突副本**。
这条策略在数据里落成两个刻意的设计：

1. **``client_record_id`` 是幂等键**：同一台设备重复提交同一条记录（弱网重试、离线队列重放）
   只会得到同一条记录，不会在库里堆出多份笔记。
2. **落败的写入也留档**（``applied=false`` + ``conflict_reason``）：LWW 的"覆盖"不是删除证据，
   被覆盖的版本必须能被人工看到——否则用户会经历"我明明写过，怎么没了"却无从查证。
   ``server_version`` 只在**生效**记录上递增；冲突副本记 0，避免污染版本序列。

``client_updated_at`` 由客户端提供，是冲突判定的依据；``created_at`` 是服务端收到的时间。
两者不能混用：设备时钟可能不准，但"谁后写"只能按客户端的业务时间判断，否则离线补传会被
当成新写入覆盖掉在线期间别人更新的内容。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk
from app.models.encrypted import EncryptedText

VISIT_OUTCOMES = ("MET", "NOT_MET", "RESCHEDULED", "CANCELLED")


class VisitRecord(Base):
    __tablename__ = "visit_records"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "client_record_id", name="uq_visit_records_org_client_record"
        ),
        UniqueConstraint(
            "organization_id", "visit_id", "id", name="uq_visit_records_org_visit_id"
        ),
        CheckConstraint(
            "outcome IN ('MET','NOT_MET','RESCHEDULED','CANCELLED')", name="outcome_allowed"
        ),
        CheckConstraint("server_version >= 0", name="server_version_non_negative"),
        Index("ix_visit_records_visit_id_client_updated_at", "visit_id", "client_updated_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    visit_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("visits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    client_record_id: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str] = mapped_column(EncryptedText(), nullable=False, server_default="")
    photos: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    follow_up_tasks: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    client_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    server_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    applied: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    conflict_reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()



class VisitFollowUp(Base):
    """Human-confirmed follow-up derived from a visit record or successful minutes AgentRun."""

    __tablename__ = "visit_follow_ups"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "trip_id", "visit_id"],
            ["visits.organization_id", "visits.trip_id", "visits.id"],
            ondelete="CASCADE",
            name="fk_visit_follow_ups_visit_same_trip",
        ),
        ForeignKeyConstraint(
            ["organization_id", "visit_id", "source_record_id"],
            ["visit_records.organization_id", "visit_records.visit_id", "visit_records.id"],
            ondelete="CASCADE",
            name="fk_visit_follow_ups_source_record",
        ),
        ForeignKeyConstraint(
            ["organization_id", "source_agent_run_id"],
            ["agent_runs.organization_id", "agent_runs.id"],
            ondelete="RESTRICT",
            name="fk_visit_follow_ups_source_agent_run",
        ),
        UniqueConstraint("organization_id", "id", name="uq_visit_follow_ups_org_id"),
        UniqueConstraint("organization_id", "visit_id", "source_key", name="uq_visit_follow_ups_source_key"),
        CheckConstraint("source_type IN ('RECORD','MINUTES_DRAFT','APPOINTMENT_RECOVERY','MANUAL')", name="source_type_allowed"),
        CheckConstraint("status IN ('OPEN','IN_PROGRESS','COMPLETED','DISMISSED')", name="status_allowed"),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint(
            "(source_type = 'RECORD' AND source_record_id IS NOT NULL AND source_agent_run_id IS NULL "
            "AND source_index IS NOT NULL AND source_index >= 0) OR "
            "(source_type = 'MINUTES_DRAFT' AND source_record_id IS NULL AND source_agent_run_id IS NOT NULL "
            "AND source_index IS NOT NULL AND source_index >= 0) OR "
            "(source_type = 'APPOINTMENT_RECOVERY' AND source_record_id IS NULL AND source_agent_run_id IS NOT NULL "
            "AND source_index IS NOT NULL AND source_index >= 0) OR "
            "(source_type = 'MANUAL' AND source_record_id IS NULL AND source_agent_run_id IS NULL "
            "AND source_index IS NULL)",
            name="source_reference_consistent",
        ),
        Index("ix_visit_follow_ups_org_visit_status", "organization_id", "visit_id", "status"),
        Index("ix_visit_follow_ups_org_trip_status", "organization_id", "trip_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    visit_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_key: Mapped[str] = mapped_column(Text, nullable=False)
    source_record_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    source_agent_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    source_index: Mapped[int | None] = mapped_column(Integer)
    source_title: Mapped[str | None] = mapped_column(Text)
    source_due_date: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    due_date: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="OPEN")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class VisitFollowUpEvent(Base):
    """Append-only actor and lifecycle audit for visit follow-ups."""

    __tablename__ = "visit_follow_up_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "follow_up_id"],
            ["visit_follow_ups.organization_id", "visit_follow_ups.id"],
            ondelete="CASCADE",
            name="fk_visit_follow_up_events_follow_up",
        ),
        UniqueConstraint("organization_id", "id", name="uq_visit_follow_up_events_org_id"),
        CheckConstraint(
            "event_type IN ('ACCEPTED','DISMISSED','CREATED_MANUAL','STARTED','COMPLETED','REOPENED','UPDATED','REASSESSED')",
            name="event_type_allowed",
        ),
        Index("ix_visit_follow_up_events_org_follow_up_created", "organization_id", "follow_up_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    follow_up_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    from_status: Mapped[str | None] = mapped_column(Text)
    to_status: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


def record_snapshot(record: VisitRecord) -> dict[str, Any]:
    """给冲突响应用的服务端/客户端快照。时间统一 ISO 字符串，便于客户端原样展示。"""
    return {
        "client_record_id": record.client_record_id,
        "outcome": record.outcome,
        "notes": record.notes,
        "client_updated_at": record.client_updated_at.isoformat(),
        "photos": list(record.photos or []),
        "follow_up_tasks": list(record.follow_up_tasks or []),
    }


__all__ = ["VISIT_OUTCOMES", "VisitRecord", "VisitFollowUp", "VisitFollowUpEvent", "record_snapshot"]
