"""Agent 记忆层模型：事实 / 过程 / 情景。对应迁移 ``0007_agent_memory``。

设计文档 v2.0 第 9 章要求三层记忆**落在同一个 PostgreSQL 事务边界内**，并且都能失效与版本化。
本模块用三张表分别承接：

- ``traveler_profiles``（事实记忆 Semantic）：出差人的稳定偏好。每次修改**新增一版**并把旧版
  ``valid_until`` 置为当前时间；历史版本不被覆盖，于是"当时为什么这么建议"可回查。
- ``plan_attempt_log``（过程记忆 Procedural）：规划生成/否决/激活的尝试记录。它回答的是
  "哪些方案试过、为什么被否"，供下一次同场景规划参考。
- ``trip_memory_summaries``（情景记忆 Episodic）：行程结束后的滚动摘要（决策、待办、教训），
  带 pgvector 向量列。向量与业务行同库同事务，不引入独立向量库。

失效语义（第 9.3 章）：所有条目都带 ``valid_from`` / ``valid_until`` / ``confidence`` /
``superseded_by``。检索只读 ``valid_until IS NULL`` 的当前条目；旧条目保留用于审计与复盘。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import get_settings
from app.models.base import Base, created_at, updated_at, uuid_pk

#: 向量维度来自配置，与迁移里的 ``vector(256)`` 必须一致。改维度等于重建索引与回填，
#: 必须走新迁移；这里只做读取。
EMBEDDING_DIM = get_settings().memory_embedding_dim

ATTEMPT_TYPES = ("PLAN_GENERATED", "PLAN_REJECTED", "PLAN_ACTIVATED", "PLAN_SUPERSEDED")
MEMORY_KINDS = ("TRIP_REVIEW", "CUSTOMER_NOTE", "PREFERENCE")


class TravelerProfile(Base):
    __tablename__ = "traveler_profiles"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "user_id", "version", name="uq_traveler_profiles_org_user_version"
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        Index("ix_traveler_profiles_org_user_valid", "organization_id", "user_id", "valid_until"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    preferences: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    notes: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="1")
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class PlanAttemptLog(Base):
    __tablename__ = "plan_attempt_log"
    __table_args__ = (
        CheckConstraint(
            "attempt_type IN ('PLAN_GENERATED','PLAN_REJECTED','PLAN_ACTIVATED','PLAN_SUPERSEDED')",
            name="attempt_type_allowed",
        ),
        Index("ix_plan_attempt_log_org_trip_created_at", "organization_id", "trip_id", "created_at"),
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
    plan_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("plan_versions.id", ondelete="SET NULL")
    )
    attempt_type: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = created_at()


class TripMemorySummary(Base):
    __tablename__ = "trip_memory_summaries"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('TRIP_REVIEW','CUSTOMER_NOTE','PREFERENCE')", name="kind_allowed"
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        Index(
            "uq_trip_memory_summaries_org_source_ref",
            "organization_id",
            "source_ref",
            unique=True,
            postgresql_where=text("source_ref IS NOT NULL"),
        ),
        Index("ix_trip_memory_summaries_org_trip", "organization_id", "trip_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trip_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    decisions: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    follow_ups: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    lessons: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.8")
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    source_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


def summary_content(item: TripMemorySummary) -> dict[str, Any]:
    return {
        "id": item.id,
        "kind": item.kind,
        "summary": item.summary,
        "decisions": list(item.decisions or []),
        "follow_ups": list(item.follow_ups or []),
        "lessons": list(item.lessons or []),
        "valid_from": item.valid_from,
        "confidence": item.confidence,
    }


__all__ = [
    "ATTEMPT_TYPES",
    "EMBEDDING_DIM",
    "MEMORY_KINDS",
    "PlanAttemptLog",
    "TravelerProfile",
    "TripMemorySummary",
    "summary_content",
]
