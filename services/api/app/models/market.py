"""市场情报领域基础模型（Sprint H1.2）。

只承载 Workspace、业务上下文版本、研究问题与活动事件。
Customer / Trip / Evidence / Workflow / AgentRun 继续复用现有模型，不在此处复制。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk

MARKET_WORKSPACE_STATUSES = ("DRAFT", "ACTIVE", "PAUSED", "COMPLETED", "ARCHIVED")
MARKET_CONTEXT_EXTRACTION_STATUSES = ("NOT_STARTED", "PENDING", "COMPLETED", "FAILED")
MARKET_QUESTION_SOURCES = ("USER", "AGENT")
MARKET_QUESTION_STATUSES = ("OPEN", "IN_PROGRESS", "ANSWERED", "BLOCKED", "CANCELLED")
MARKET_ACTIVITY_ACTORS = ("USER", "SYSTEM")


class MarketWorkspace(Base):
    __tablename__ = "market_workspaces"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_market_workspaces_org_id"),
        CheckConstraint(
            "status IN ('DRAFT','ACTIVE','PAUSED','COMPLETED','ARCHIVED')",
            name="status_allowed",
        ),
        Index("ix_market_workspaces_org_status_created", "organization_id", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    objective: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="DRAFT")
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    current_context_version: Mapped[int | None] = mapped_column(Integer)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class MarketContextSnapshot(Base):
    __tablename__ = "market_context_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["market_workspaces.organization_id", "market_workspaces.id"],
            ondelete="CASCADE",
            name="fk_market_context_workspace_same_org",
        ),
        UniqueConstraint(
            "organization_id", "workspace_id", "version",
            name="uq_market_context_workspace_version",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "extraction_status IN ('NOT_STARTED','PENDING','COMPLETED','FAILED')",
            name="extraction_status_allowed",
        ),
        Index("ix_market_context_org_workspace_created", "organization_id", "workspace_id", "created_at"),
        Index(
            "uq_market_context_current",
            "organization_id",
            "workspace_id",
            unique=True,
            postgresql_where=text("is_current = true"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    raw_input: Mapped[str] = mapped_column(Text, nullable=False)
    extracted_context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    user_overrides: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    unknowns: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    provenance: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    extraction_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="NOT_STARTED"
    )
    extraction_prompt_id: Mapped[str | None] = mapped_column(Text)
    extraction_prompt_version: Mapped[str | None] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()


class MarketResearchQuestion(Base):
    __tablename__ = "market_research_questions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["market_workspaces.organization_id", "market_workspaces.id"],
            ondelete="CASCADE",
            name="fk_market_question_workspace_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workspace_id", "parent_question_id"],
            [
                "market_research_questions.organization_id",
                "market_research_questions.workspace_id",
                "market_research_questions.id",
            ],
            ondelete="CASCADE",
            name="fk_market_question_parent_same_workspace",
        ),
        UniqueConstraint(
            "organization_id", "workspace_id", "id",
            name="uq_market_questions_org_workspace_id",
        ),
        CheckConstraint("priority >= 1 AND priority <= 5", name="priority_range"),
        CheckConstraint(
            "source IN ('USER','AGENT')", name="source_allowed"
        ),
        CheckConstraint(
            "status IN ('OPEN','IN_PROGRESS','ANSWERED','BLOCKED','CANCELLED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "parent_question_id IS NULL OR parent_question_id <> id",
            name="parent_not_self",
        ),
        Index(
            "ix_market_questions_org_workspace_status_priority",
            "organization_id", "workspace_id", "status", "priority",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    parent_question_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    question: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="USER")
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="3")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="OPEN")
    acceptance_criteria: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    answer_summary: Mapped[str | None] = mapped_column(Text)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class MarketQuestionDependency(Base):
    __tablename__ = "market_question_dependencies"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workspace_id", "question_id"],
            [
                "market_research_questions.organization_id",
                "market_research_questions.workspace_id",
                "market_research_questions.id",
            ],
            ondelete="CASCADE",
            name="fk_market_question_dependency_question",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workspace_id", "depends_on_question_id"],
            [
                "market_research_questions.organization_id",
                "market_research_questions.workspace_id",
                "market_research_questions.id",
            ],
            ondelete="CASCADE",
            name="fk_market_question_dependency_target",
        ),
        UniqueConstraint(
            "organization_id", "question_id", "depends_on_question_id",
            name="uq_market_question_dependency",
        ),
        CheckConstraint("question_id <> depends_on_question_id", name="not_self_dependency"),
        Index("ix_market_question_dependencies_workspace", "organization_id", "workspace_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    question_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    depends_on_question_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = created_at()


class MarketActivityEvent(Base):
    __tablename__ = "market_activity_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["market_workspaces.organization_id", "market_workspaces.id"],
            ondelete="CASCADE",
            name="fk_market_activity_workspace_same_org",
        ),
        CheckConstraint("actor_type IN ('USER','SYSTEM')", name="actor_type_allowed"),
        Index(
            "ix_market_activity_org_workspace_created",
            "organization_id", "workspace_id", "created_at",
        ),
        Index(
            "ix_market_activity_org_aggregate",
            "organization_id", "aggregate_type", "aggregate_id",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="USER")
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    aggregate_type: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = created_at()


__all__ = [
    "MARKET_ACTIVITY_ACTORS",
    "MARKET_CONTEXT_EXTRACTION_STATUSES",
    "MARKET_QUESTION_SOURCES",
    "MARKET_QUESTION_STATUSES",
    "MARKET_WORKSPACE_STATUSES",
    "MarketActivityEvent",
    "MarketContextSnapshot",
    "MarketQuestionDependency",
    "MarketResearchQuestion",
    "MarketWorkspace",
]
