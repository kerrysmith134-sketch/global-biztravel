"""市场情报领域基础模型（Sprint H1.2）。

只承载 Workspace、业务上下文版本、研究问题与活动事件。
Customer / Trip / Evidence / Workflow / AgentRun 继续复用现有模型，不在此处复制。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
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
MARKET_INSIGHT_STATUSES = ("DRAFT", "NEEDS_REVIEW", "ACCEPTED", "REJECTED", "CONFLICTED", "SUPERSEDED")
MARKET_INSIGHT_CONFIDENCE_LEVELS = ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT")
MARKET_EVIDENCE_RELATIONS = ("SUPPORTS", "CONTRADICTS", "CONTEXT")
MARKET_OPPORTUNITY_STATUSES = (
    "DISCOVERED",
    "RESEARCHING",
    "EVIDENCE_READY",
    "QUALIFICATION_PENDING",
    "QUALIFIED",
    "ACTION_PLANNED",
    "WON",
    "LOST",
    "DISQUALIFIED",
    "ON_HOLD",
)
MARKET_ASSESSMENT_CONFIDENCE = ("HIGH", "MEDIUM", "LOW", "INSUFFICIENT")


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


class MarketResearchQuestionAnswer(Base):
    """Versioned answer draft for a Workspace research question; acceptance is human-governed."""

    __tablename__ = "market_research_question_answers"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workspace_id", "question_id"],
            [
                "market_research_questions.organization_id",
                "market_research_questions.workspace_id",
                "market_research_questions.id",
            ],
            ondelete="CASCADE",
            name="fk_market_question_answer_question_same_workspace",
        ),
        ForeignKeyConstraint(
            ["organization_id", "generated_by_run_id"],
            ["agent_runs.organization_id", "agent_runs.id"],
            ondelete="RESTRICT",
            name="fk_market_question_answer_run_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workspace_id", "supersedes_id"],
            [
                "market_research_question_answers.organization_id",
                "market_research_question_answers.workspace_id",
                "market_research_question_answers.id",
            ],
            ondelete="NO ACTION",
            name="fk_market_question_answer_supersedes_same_workspace",
        ),
        UniqueConstraint(
            "organization_id", "question_id", "version",
            name="uq_market_question_answer_version",
        ),
        UniqueConstraint(
            "organization_id", "workspace_id", "id",
            name="uq_market_question_answer_org_workspace_id",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "status IN ('DRAFT','NEEDS_REVIEW','CONFLICTED','ACCEPTED','REJECTED','SUPERSEDED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "confidence_level IN ('HIGH','MEDIUM','LOW','INSUFFICIENT')",
            name="confidence_allowed",
        ),
        CheckConstraint("length(btrim(answer_summary)) > 0", name="answer_not_blank"),
        CheckConstraint(
            "status <> 'ACCEPTED' OR (reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="accepted_has_reviewer",
        ),
        CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id", name="supersedes_not_self"),
        Index(
            "ix_market_question_answers_org_question_version",
            "organization_id", "question_id", "version",
        ),
        Index(
            "ix_market_question_answers_one_accepted",
            "organization_id", "workspace_id", "question_id",
            unique=True,
            postgresql_where=text("status = 'ACCEPTED'"),
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
    question_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    answer_summary: Mapped[str] = mapped_column(Text, nullable=False)
    assumptions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    unknowns: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    confidence_level: Mapped[str] = mapped_column(Text, nullable=False, server_default="INSUFFICIENT")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="NEEDS_REVIEW")
    evidence_gap: Mapped[str | None] = mapped_column(Text)
    generated_by_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class MarketResearchQuestionAnswerEvidence(Base):
    """Explicit evidence role attached to one immutable answer version."""

    __tablename__ = "market_research_question_answer_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workspace_id", "answer_id"],
            [
                "market_research_question_answers.organization_id",
                "market_research_question_answers.workspace_id",
                "market_research_question_answers.id",
            ],
            ondelete="CASCADE",
            name="fk_market_question_answer_evidence_answer_same_workspace",
        ),
        ForeignKeyConstraint(
            ["organization_id", "evidence_id"],
            ["evidence.organization_id", "evidence.id"],
            ondelete="RESTRICT",
            name="fk_market_question_answer_evidence_evidence_same_org",
        ),
        UniqueConstraint(
            "organization_id", "answer_id", "evidence_id", "relation_type",
            name="uq_market_question_answer_evidence_relation",
        ),
        CheckConstraint(
            "relation_type IN ('SUPPORTS','CONTRADICTS','CONTEXT')",
            name="relation_type_allowed",
        ),
        Index(
            "ix_market_question_answer_evidence_org_answer",
            "organization_id", "answer_id",
        ),
        Index(
            "ix_market_question_answer_evidence_org_evidence",
            "organization_id", "evidence_id",
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
    answer_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    evidence_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    relation_type: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


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



class MarketWorkspaceWorkflow(Base):
    """Connect a long-lived Market Workspace to one or more existing workflows."""

    __tablename__ = "market_workspace_workflows"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["market_workspaces.organization_id", "market_workspaces.id"],
            ondelete="CASCADE",
            name="fk_market_workspace_workflow_workspace_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workflow_id"],
            ["workflow_instances.organization_id", "workflow_instances.id"],
            ondelete="CASCADE",
            name="fk_market_workspace_workflow_instance_same_org",
        ),
        UniqueConstraint(
            "organization_id", "workspace_id", "workflow_id",
            name="uq_market_workspace_workflow",
        ),
        Index(
            "ix_market_workspace_workflows_org_workspace",
            "organization_id", "workspace_id",
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
    workflow_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    linked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()



class MarketResearchPlanEvidence(Base):
    """Links a versioned Workspace source to every ResearchPlan/query that discovered it."""

    __tablename__ = "market_research_plan_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workspace_id", "research_plan_id"],
            [
                "market_workspace_workflows.organization_id",
                "market_workspace_workflows.workspace_id",
                "market_workspace_workflows.workflow_id",
            ],
            ondelete="CASCADE",
            name="fk_market_research_plan_evidence_plan_workspace",
        ),
        ForeignKeyConstraint(
            ["organization_id", "evidence_id"],
            ["evidence.organization_id", "evidence.id"],
            ondelete="RESTRICT",
            name="fk_market_research_plan_evidence_evidence_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "collected_by_run_id"],
            ["agent_runs.organization_id", "agent_runs.id"],
            ondelete="RESTRICT",
            name="fk_market_research_plan_evidence_run_same_org",
        ),
        UniqueConstraint(
            "organization_id", "workspace_id", "research_plan_id",
            "evidence_id", "query_sha256",
            name="uq_market_research_plan_evidence_query",
        ),
        CheckConstraint("length(btrim(search_query)) > 0", name="search_query_not_blank"),
        CheckConstraint("length(query_sha256) = 64", name="query_sha256_length"),
        Index(
            "ix_market_research_plan_evidence_org_plan",
            "organization_id", "workspace_id", "research_plan_id", "created_at",
        ),
        Index(
            "ix_market_research_plan_evidence_org_evidence",
            "organization_id", "evidence_id",
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
    research_plan_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    evidence_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    search_query: Mapped[str] = mapped_column(Text, nullable=False)
    query_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    collected_by_run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = created_at()


class MarketEntity(Base):
    """A research candidate entity; it is not a CRM Customer record."""

    __tablename__ = "market_entities"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_market_entities_org_id"),
        CheckConstraint(
            "status IN ('CANDIDATE','REVIEWED','VERIFIED','REJECTED','MERGED')",
            name="status_allowed",
        ),
        Index("ix_market_entities_org_domain", "organization_id", "canonical_domain"),
        Index("ix_market_entities_org_kind_name", "organization_id", "entity_kind", "normalized_name"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_kind: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    canonical_domain: Mapped[str | None] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(CHAR(2))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="CANDIDATE")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class MarketEntityAlias(Base):
    """Preserves raw names, translated labels, domains and external identifiers."""

    __tablename__ = "market_entity_aliases"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "entity_id"],
            ["market_entities.organization_id", "market_entities.id"],
            ondelete="CASCADE",
            name="fk_market_entity_alias_entity_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "source_evidence_id"],
            ["evidence.organization_id", "evidence.id"],
            ondelete="RESTRICT",
            name="fk_market_entity_alias_evidence_same_org",
        ),
        UniqueConstraint(
            "organization_id", "entity_id", "alias_type", "normalized_value",
            name="uq_market_entity_alias_value",
        ),
        Index("ix_market_entity_aliases_org_lookup", "organization_id", "alias_type", "normalized_value"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    alias_type: Mapped[str] = mapped_column(Text, nullable=False)
    raw_value: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_value: Mapped[str] = mapped_column(Text, nullable=False)
    source_evidence_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    created_at: Mapped[datetime] = created_at()


class MarketWorkspaceEntity(Base):
    """Associates a shared candidate entity with one Workspace's research scope."""

    __tablename__ = "market_workspace_entities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["market_workspaces.organization_id", "market_workspaces.id"],
            ondelete="CASCADE",
            name="fk_market_workspace_entity_workspace_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "entity_id"],
            ["market_entities.organization_id", "market_entities.id"],
            ondelete="RESTRICT",
            name="fk_market_workspace_entity_entity_same_org",
        ),
        UniqueConstraint(
            "organization_id", "workspace_id", "entity_id",
            name="uq_market_workspace_entity",
        ),
        CheckConstraint(
            "status IN ('DISCOVERED','RESEARCHING','INCLUDED','EXCLUDED')",
            name="status_allowed",
        ),
        Index("ix_market_workspace_entities_org_workspace", "organization_id", "workspace_id"),
        Index("ix_market_workspace_entities_org_entity", "organization_id", "entity_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    discovery_reason: Mapped[str | None] = mapped_column(Text)
    relevance_note: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="DISCOVERED")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class MarketEntityCustomerLink(Base):
    """A candidate match to a real Customer; only explicit approval confirms the link."""

    __tablename__ = "market_entity_customer_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "entity_id"],
            ["market_entities.organization_id", "market_entities.id"],
            ondelete="CASCADE",
            name="fk_market_entity_customer_link_entity_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "customer_id"],
            ["customers.organization_id", "customers.id"],
            ondelete="RESTRICT",
            name="fk_market_entity_customer_link_customer_same_org",
        ),
        UniqueConstraint(
            "organization_id", "entity_id", "customer_id",
            name="uq_market_entity_customer_link",
        ),
        CheckConstraint(
            "link_status IN ('MATCH_CANDIDATE','USER_CONFIRMED','REJECTED','CONFLICTED')",
            name="link_status_allowed",
        ),
        CheckConstraint(
            "link_status <> 'USER_CONFIRMED' OR "
            "(confirmed_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="confirmed_link_has_actor_and_time",
        ),
        Index("ix_market_entity_customer_links_org_customer", "organization_id", "customer_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    customer_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    link_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="MATCH_CANDIDATE")
    matching_basis: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    confirmed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


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


class MarketInsight(Base):
    """Immutable, versioned market judgment; only its review state may transition."""

    __tablename__ = "market_insights"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["market_workspaces.organization_id", "market_workspaces.id"],
            ondelete="CASCADE",
            name="fk_market_insight_workspace_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "generated_by_run_id"],
            ["agent_runs.organization_id", "agent_runs.id"],
            ondelete="RESTRICT",
            name="fk_market_insight_agent_run_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workspace_id", "supersedes_id"],
            ["market_insights.organization_id", "market_insights.workspace_id", "market_insights.id"],
            ondelete="NO ACTION",
            name="fk_market_insight_supersedes_same_workspace",
        ),
        UniqueConstraint("organization_id", "id", name="uq_market_insights_org_id"),
        UniqueConstraint("organization_id", "workspace_id", "id", name="uq_market_insights_org_workspace_id"),
        UniqueConstraint(
            "organization_id", "workspace_id", "insight_key", "version",
            name="uq_market_insights_workspace_key_version",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "status IN ('DRAFT','NEEDS_REVIEW','ACCEPTED','REJECTED','CONFLICTED','SUPERSEDED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "confidence_level IN ('HIGH','MEDIUM','LOW','INSUFFICIENT')",
            name="confidence_allowed",
        ),
        CheckConstraint("length(btrim(statement)) > 0", name="statement_not_blank"),
        CheckConstraint(
            "status <> 'ACCEPTED' OR (reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL)",
            name="accepted_has_reviewer",
        ),
        CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id", name="supersedes_not_self"),
        Index("ix_market_insights_org_workspace_created", "organization_id", "workspace_id", "created_at"),
        Index("ix_market_insights_org_workspace_key", "organization_id", "workspace_id", "insight_key", "version"),
        Index(
            "uq_market_insights_one_accepted_per_key",
            "organization_id", "workspace_id", "insight_key",
            unique=True,
            postgresql_where=text("status = 'ACCEPTED'"),
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
    insight_key: Mapped[str] = mapped_column(Text, nullable=False)
    insight_type: Mapped[str] = mapped_column(Text, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    assumptions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    unknowns: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    implications: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    confidence_level: Mapped[str] = mapped_column(Text, nullable=False, server_default="INSUFFICIENT")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="NEEDS_REVIEW")
    generated_by_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    insufficient_evidence_reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class MarketInsightEvidence(Base):
    """Explicit support / contradiction / context relation for one Insight."""

    __tablename__ = "market_insight_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "insight_id"],
            ["market_insights.organization_id", "market_insights.id"],
            ondelete="CASCADE",
            name="fk_market_insight_evidence_insight_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "evidence_id"],
            ["evidence.organization_id", "evidence.id"],
            ondelete="RESTRICT",
            name="fk_market_insight_evidence_evidence_same_org",
        ),
        UniqueConstraint(
            "organization_id", "insight_id", "evidence_id", "relation_type",
            name="uq_market_insight_evidence_relation",
        ),
        CheckConstraint(
            "relation_type IN ('SUPPORTS','CONTRADICTS','CONTEXT')",
            name="relation_type_allowed",
        ),
        Index("ix_market_insight_evidence_org_insight", "organization_id", "insight_id"),
        Index("ix_market_insight_evidence_org_evidence", "organization_id", "evidence_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    insight_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    evidence_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    relation_type: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class MarketOpportunity(Base):
    """Evidence-backed commercial opportunity; qualification and outcomes are human-governed."""

    __tablename__ = "market_opportunities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["market_workspaces.organization_id", "market_workspaces.id"],
            ondelete="CASCADE",
            name="fk_market_opportunity_workspace_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "created_by_run_id"],
            ["agent_runs.organization_id", "agent_runs.id"],
            ondelete="RESTRICT",
            name="fk_market_opportunity_agent_run_same_org",
        ),
        UniqueConstraint("organization_id", "id", name="uq_market_opportunities_org_id"),
        UniqueConstraint(
            "organization_id", "workspace_id", "id",
            name="uq_market_opportunities_org_workspace_id",
        ),
        CheckConstraint(
            "status IN ('DISCOVERED','RESEARCHING','EVIDENCE_READY','QUALIFICATION_PENDING',"
            "'QUALIFIED','ACTION_PLANNED','WON','LOST','DISQUALIFIED','ON_HOLD')",
            name="status_allowed",
        ),
        CheckConstraint("assessment_version >= 0", name="assessment_version_non_negative"),
        CheckConstraint(
            "assessment_confidence IN ('HIGH','MEDIUM','LOW','INSUFFICIENT')",
            name="assessment_confidence_allowed",
        ),
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        CheckConstraint(
            "status NOT IN ('QUALIFIED','ACTION_PLANNED','WON','LOST') OR "
            "(qualified_by_user_id IS NOT NULL AND qualified_at IS NOT NULL "
            "AND qualification_reason IS NOT NULL AND length(btrim(qualification_reason)) > 0)",
            name="qualified_has_human_approval",
        ),
        CheckConstraint(
            "status NOT IN ('WON','LOST') OR "
            "(outcome_recorded_by_user_id IS NOT NULL AND outcome_recorded_at IS NOT NULL "
            "AND outcome_note IS NOT NULL AND length(btrim(outcome_note)) > 0)",
            name="terminal_outcome_has_actor_and_note",
        ),
        CheckConstraint(
            "status <> 'DISQUALIFIED' OR (disqualification_reason IS NOT NULL "
            "AND length(btrim(disqualification_reason)) > 0)",
            name="disqualified_has_reason",
        ),
        Index("ix_market_opportunities_org_workspace_status", "organization_id", "workspace_id", "status"),
        Index("ix_market_opportunities_org_workspace_updated", "organization_id", "workspace_id", "updated_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="DISCOVERED")
    next_action: Mapped[str | None] = mapped_column(Text)
    qualification_reason: Mapped[str | None] = mapped_column(Text)
    disqualification_reason: Mapped[str | None] = mapped_column(Text)
    assessment_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    assessment_dimensions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    assessment_summary: Mapped[str | None] = mapped_column(Text)
    assessment_confidence: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="INSUFFICIENT"
    )
    created_by_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    qualified_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    qualified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome_recorded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    outcome_recorded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class MarketOpportunityAssessment(Base):
    """Append-only assessment snapshot; the Opportunity row only contains the current projection."""

    __tablename__ = "market_opportunity_assessments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workspace_id", "opportunity_id"],
            [
                "market_opportunities.organization_id",
                "market_opportunities.workspace_id",
                "market_opportunities.id",
            ],
            ondelete="CASCADE",
            name="fk_market_opp_assessment_opportunity_same_workspace",
        ),
        ForeignKeyConstraint(
            ["organization_id", "assessed_by_run_id"],
            ["agent_runs.organization_id", "agent_runs.id"],
            ondelete="RESTRICT",
            name="fk_market_opp_assessment_agent_run_same_org",
        ),
        UniqueConstraint(
            "organization_id", "opportunity_id", "version",
            name="uq_market_opp_assessment_version",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            "confidence_level IN ('HIGH','MEDIUM','LOW','INSUFFICIENT')",
            name="confidence_allowed",
        ),
        Index(
            "ix_market_opp_assessment_org_opp_created",
            "organization_id", "opportunity_id", "created_at",
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
    opportunity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    dimensions: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    confidence_level: Mapped[str] = mapped_column(Text, nullable=False)
    assessed_by_run_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    assessed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()


class MarketOpportunityEvidence(Base):
    """Explicit evidence role attached to one commercial opportunity."""

    __tablename__ = "market_opportunity_evidence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workspace_id", "opportunity_id"],
            [
                "market_opportunities.organization_id",
                "market_opportunities.workspace_id",
                "market_opportunities.id",
            ],
            ondelete="CASCADE",
            name="fk_market_opp_evidence_opportunity_same_workspace",
        ),
        ForeignKeyConstraint(
            ["organization_id", "evidence_id"],
            ["evidence.organization_id", "evidence.id"],
            ondelete="RESTRICT",
            name="fk_market_opp_evidence_evidence_same_org",
        ),
        UniqueConstraint(
            "organization_id", "opportunity_id", "evidence_id", "relation_type",
            name="uq_market_opportunity_evidence_relation",
        ),
        CheckConstraint(
            "relation_type IN ('SUPPORTS','CONTRADICTS','CONTEXT')",
            name="relation_type_allowed",
        ),
        Index("ix_market_opp_evidence_org_opp", "organization_id", "opportunity_id"),
        Index("ix_market_opp_evidence_org_evidence", "organization_id", "evidence_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    evidence_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    relation_type: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class MarketOpportunityEntity(Base):
    """Explicit relation from an Opportunity to a candidate MarketEntity in its Workspace."""

    __tablename__ = "market_opportunity_entities"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workspace_id", "opportunity_id"],
            [
                "market_opportunities.organization_id",
                "market_opportunities.workspace_id",
                "market_opportunities.id",
            ],
            ondelete="CASCADE",
            name="fk_market_opp_entity_opportunity_same_workspace",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workspace_id", "entity_id"],
            [
                "market_workspace_entities.organization_id",
                "market_workspace_entities.workspace_id",
                "market_workspace_entities.entity_id",
            ],
            ondelete="CASCADE",
            name="fk_market_opp_entity_workspace_entity",
        ),
        UniqueConstraint(
            "organization_id", "opportunity_id", "entity_id",
            name="uq_market_opportunity_entity",
        ),
        Index("ix_market_opp_entities_org_workspace_entity", "organization_id", "workspace_id", "entity_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    opportunity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    relationship_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class MarketWorkspaceTripLink(Base):
    """Historical, reversible many-to-many association; never owns or deletes a Trip."""

    __tablename__ = "market_workspace_trip_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["market_workspaces.organization_id", "market_workspaces.id"],
            ondelete="CASCADE",
            name="fk_market_workspace_trip_workspace_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "trip_id"],
            ["trips.organization_id", "trips.id"],
            ondelete="RESTRICT",
            name="fk_market_workspace_trip_trip_same_org",
        ),
        UniqueConstraint(
            "organization_id", "workspace_id", "trip_id",
            name="uq_market_workspace_trip_link",
        ),
        CheckConstraint("link_status IN ('LINKED','UNLINKED')", name="link_status_allowed"),
        CheckConstraint(
            "link_status <> 'UNLINKED' OR (unlinked_by_user_id IS NOT NULL AND unlinked_at IS NOT NULL)",
            name="unlinked_has_actor_and_time",
        ),
        Index("ix_market_workspace_trip_org_trip", "organization_id", "trip_id"),
        Index("ix_market_workspace_trip_org_workspace_status", "organization_id", "workspace_id", "link_status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    trip_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    link_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="LINKED")
    link_note: Mapped[str | None] = mapped_column(Text)
    linked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    unlinked_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT")
    )
    unlinked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


__all__ = [
    "MARKET_ACTIVITY_ACTORS",
    "MARKET_CONTEXT_EXTRACTION_STATUSES",
    "MARKET_QUESTION_SOURCES",
    "MARKET_QUESTION_STATUSES",
    "MARKET_WORKSPACE_STATUSES",
    "MARKET_INSIGHT_STATUSES",
    "MARKET_INSIGHT_CONFIDENCE_LEVELS",
    "MARKET_ASSESSMENT_CONFIDENCE",
    "MARKET_OPPORTUNITY_STATUSES",
    "MARKET_EVIDENCE_RELATIONS",
    "MarketActivityEvent",
    "MarketEntity",
    "MarketEntityAlias",
    "MarketEntityCustomerLink",
    "MarketInsight",
    "MarketInsightEvidence",
    "MarketWorkspaceTripLink",
    "MarketOpportunityEntity",
    "MarketOpportunityEvidence",
    "MarketOpportunityAssessment",
    "MarketOpportunity",
    "MarketContextSnapshot",
    "MarketQuestionDependency",
    "MarketResearchQuestion",
    "MarketResearchQuestionAnswer",
    "MarketResearchQuestionAnswerEvidence",
    "MarketResearchPlanEvidence",
    "MarketWorkspace",
    "MarketWorkspaceEntity",
    "MarketWorkspaceWorkflow",
]
