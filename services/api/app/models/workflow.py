"""工作流与审计扩展模型。对应迁移 ``0016_workflow_evidence_sync``。

PRD MVP-1 DDL/迁移 0005/0006 与设计文档 §12.1 明确保留这些表：

- ``workflow_instances``：一次多步业务流程（如计划变更）的持久化实例；
- ``agent_runs``：一次 Agent/LLM 任务的输入输出与状态；
- ``evidence``：可复用的证据条目（不再只塞在 JSON 字段里）；
- ``sync_operations``：离线同步的逐项操作记录；
- ``integration_connections``：外部供应商连接与能力状态；
- ``external_actions``：对外动作的请求/响应与审批链。

这些表不是装饰：计划变更会写 workflow，同步会逐项落 sync_operations，建议执行会落
external_action，建议证据会落 evidence。表结构统一带 ``organization_id`` 并启用 RLS。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk

WORKFLOW_STATUSES = ("ACCEPTED", "RUNNING", "COMPLETED", "FAILED", "REJECTED")
AGENT_RUN_STATUSES = ("RUNNING", "SUCCEEDED", "FAILED")
SYNC_STATUSES = ("APPLIED", "CONFLICT", "REJECTED")
CONNECTION_STATUSES = ("ACTIVE", "DISABLED", "DEGRADED")
ACTION_STATUSES = ("PREPARED", "APPROVED", "EXECUTED", "FAILED")


class WorkflowInstance(Base):
    __tablename__ = "workflow_instances"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACCEPTED','RUNNING','COMPLETED','FAILED','REJECTED')",
            name="status_allowed",
        ),
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
    workflow_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="ACCEPTED")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("background_jobs.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class AgentRun(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING','SUCCEEDED','FAILED')", name="status_allowed"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workflow_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("workflow_instances.id", ondelete="SET NULL"), index=True
    )
    agent_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="RUNNING")
    input: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    output: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class Evidence(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "subject_type", "subject_id", "kind", "ref",
            name="uq_evidence_org_subject_kind_ref",
        ),
        Index("ix_evidence_org_subject", "organization_id", "subject_type", "subject_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    ref: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class SyncOperation(Base):
    __tablename__ = "sync_operations"
    __table_args__ = (
        UniqueConstraint("organization_id", "operation_id", name="uq_sync_operations_org_operation"),
        CheckConstraint(
            "status IN ('APPLIED','CONFLICT','REJECTED')", name="status_allowed"
        ),
        Index("ix_sync_operations_org_trip", "organization_id", "trip_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trip_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trips.id", ondelete="SET NULL"), index=True
    )
    device_id: Mapped[str | None] = mapped_column(Text)
    operation_id: Mapped[str] = mapped_column(Text, nullable=False)
    operation_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    client_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class IntegrationConnection(Base):
    __tablename__ = "integration_connections"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "provider", "capability",
            name="uq_integration_connections_org_provider_capability",
        ),
        CheckConstraint(
            "status IN ('ACTIVE','DISABLED','DEGRADED')", name="status_allowed"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    capability: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="DISABLED")
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class ExternalAction(Base):
    __tablename__ = "external_actions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PREPARED','APPROVED','EXECUTED','FAILED')", name="status_allowed"
        ),
        Index("ix_external_actions_org_subject", "organization_id", "subject_type", "subject_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    action_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="PREPARED")
    request: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    response: Mapped[dict | None] = mapped_column(JSONB)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


__all__ = [
    "ACTION_STATUSES",
    "AGENT_RUN_STATUSES",
    "AgentRun",
    "CONNECTION_STATUSES",
    "Evidence",
    "ExternalAction",
    "IntegrationConnection",
    "SYNC_STATUSES",
    "SyncOperation",
    "WORKFLOW_STATUSES",
    "WorkflowInstance",
]
