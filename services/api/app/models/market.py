"""通用市场情报领域模型的 H1.2 首批基础实体。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    Text,
    text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk


class MarketWorkspace(Base):
    __tablename__ = "market_workspaces"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','ACTIVE','PAUSED','COMPLETED','ARCHIVED')",
            name="status_allowed",
        ),
        UniqueConstraint("organization_id", "id", name="uq_market_workspaces_org_id"),
        Index("ix_market_workspaces_org_status_updated", "organization_id", "status", "updated_at"),
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
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    current_context_version: Mapped[int | None] = mapped_column(Integer)
    settings: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class MarketContextSnapshot(Base):
    __tablename__ = "market_context_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "workspace_id", "version",
            name="uq_market_context_org_workspace_version",
        ),
        UniqueConstraint("organization_id", "id", name="uq_market_context_org_id"),
        ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["market_workspaces.organization_id", "market_workspaces.id"],
            name="fk_market_context_workspace_same_org",
            ondelete="CASCADE",
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
            postgresql_where=text("is_current"),
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
    extracted_context: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    user_overrides: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    unknowns: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
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
        CheckConstraint("priority BETWEEN 1 AND 5", name="priority_range"),
        CheckConstraint(
            "status IN ('OPEN','IN_PROGRESS','ANSWERED','BLOCKED','CANCELLED')",
            name="status_allowed",
        ),
        CheckConstraint("source IN ('USER','AGENT')", name="source_allowed"),
        UniqueConstraint(
            "organization_id", "workspace_id", "id",
            name="uq_market_questions_org_workspace_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["market_workspaces.organization_id", "market_workspaces.id"],
            name="fk_market_questions_workspace_same_org",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workspace_id", "parent_question_id"],
            [
                "market_research_questions.organization_id",
                "market_research_questions.workspace_id",
                "market_research_questions.id",
            ],
            name="fk_market_questions_parent_same_workspace",
            ondelete="CASCADE",
        ),
        Index(
            "ix_market_questions_org_workspace_priority",
            "organization_id",
            "workspace_id",
            "priority",
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
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    parent_question_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    question: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="USER")
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="3")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="OPEN")
    acceptance_criteria: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    answer_summary: Mapped[str | None] = mapped_column(Text)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class MarketActivityEvent(Base):
    __tablename__ = "market_activity_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["market_workspaces.organization_id", "market_workspaces.id"],
            name="fk_market_activity_workspace_same_org",
            ondelete="CASCADE",
        ),
        Index(
            "ix_market_activity_org_workspace_created",
            "organization_id",
            "workspace_id",
            "created_at",
        ),
        Index(
            "ix_market_activity_org_aggregate",
            "organization_id",
            "aggregate_type",
            "aggregate_id",
        ),
        CheckConstraint("actor_type IN ('USER','SYSTEM')", name="actor_type_allowed"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    aggregate_type: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = created_at()
