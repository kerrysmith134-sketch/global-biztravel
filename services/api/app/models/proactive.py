"""主动式运行时模型：触发、建议、审计与审批。对应迁移 ``0005_proactive_runtime``。

设计文档 v2.0 第 8 章把"主动式"定义成一条可审计的闭环，而不是某个定时任务：
``DETECTED → EVALUATED → PROPOSED → {SUPPRESSED | APPROVED → EXECUTED} → AUDITED → LEARNED``。
本模块的四张表就是这条闭环的落点：

- ``proactive_triggers``：**感知**。任何触发源（SCHEDULED / EVENT / STATE / EXTERNAL）先落一条
  触发记录；``(organization_id, trip_id, type, rule_id, dedupe_key)`` 唯一，因此同一次感知
  在重复扫描或重复事件下只被登记一次。
- ``proactive_suggestions``：**提案**。一条建议必须能回答四件事：为什么（``rationale``）、
  凭什么（``evidence``）、打算做什么（``proposed_action``）、是否需要人审（``requires_approval``）。
  它与触发记录用 ``dedupe_key`` 唯一约束绑定，同一去重键不可能产生第二条建议。
- ``suggestion_audit``：**审计**。每个生命周期动作一条记录，含执行人/系统与结构化细节。
- ``approvals``：**人审证据**。设计文档 §8.5 要求"执行前必须有一条 approval 记录（人、时间、
  决策）"，这里用一张通用审批表承载，后续多级审批可复用它而不是再造一套。

状态词与 API 的关系：数据库里的 ``PENDING`` 等价于设计文档状态机里的 ``PROPOSED``——
客户端契约（``apps/ios`` 的 ``SuggestedSuggestion.isActionable``）只认 ``PENDING``，
所以对外统一用 ``PENDING``，避免同一件事在两处有两套叫法。

几条刻意的约束：

- ``requires_approval`` 默认 ``true``。主动系统只建议与准备，对外动作一律人审（不变量 I3）；
  把默认值设成 ``false`` 等于给未来某次误操作开了后门。
- ``push_status`` 与 ``status`` 分开：低置信度、冷却期或超出每日打扰上限的建议**仍然入库**
  （可被复盘与指标统计），但 ``WITHHELD`` 表示不推送给用户。两者混成一个状态会让
  "为什么用户没看到"无法解释。
- ``feedback_penalty`` 记录误报回收对置信度的折扣，使 AT-P04 的"同类建议权重下降"可观测，
  而不是只写一句"已学习"。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk

TRIGGER_TYPES = ("SCHEDULED", "EVENT", "STATE", "EXTERNAL")
TRIGGER_STATUSES = ("DETECTED", "EVALUATED", "PROPOSED", "SUPPRESSED", "DEDUPED", "FAILED")
SUGGESTION_STATUSES = ("PENDING", "SUPPRESSED", "APPROVED", "EXECUTED", "DISMISSED", "EXPIRED")
PUSH_STATUSES = ("PENDING", "PUSHED", "WITHHELD")
SEVERITIES = ("LOW", "MEDIUM", "HIGH")
AUDIT_ACTIONS = (
    "DETECTED",
    "EVALUATED",
    "PROPOSED",
    "SUPPRESSED",
    "APPROVED",
    "EXECUTED",
    "DISMISSED",
    "AUDITED",
    "LEARNED",
)
APPROVAL_DECISIONS = ("APPROVED", "REJECTED")


class ProactiveTrigger(Base):
    __tablename__ = "proactive_triggers"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "trip_id",
            "type",
            "rule_id",
            "dedupe_key",
            name="uq_proactive_triggers_org_trip_rule_dedupe",
        ),
        CheckConstraint(
            "type IN ('SCHEDULED','EVENT','STATE','EXTERNAL')", name="type_allowed"
        ),
        CheckConstraint(
            "status IN ('DETECTED','EVALUATED','PROPOSED','SUPPRESSED','DEDUPED','FAILED')",
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
    trip_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    rule_id: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="DETECTED")
    suggestion_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class ProactiveSuggestion(Base):
    __tablename__ = "proactive_suggestions"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "trip_id",
            "suggestion_type",
            "dedupe_key",
            name="uq_proactive_suggestions_org_trip_type_dedupe",
        ),
        CheckConstraint(
            "status IN ('PENDING','SUPPRESSED','APPROVED','EXECUTED','DISMISSED','EXPIRED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "push_status IN ('PENDING','PUSHED','WITHHELD')", name="push_status_allowed"
        ),
        CheckConstraint("severity IN ('LOW','MEDIUM','HIGH')", name="severity_allowed"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint("feedback_penalty >= 0 AND feedback_penalty <= 1", name="penalty_range"),
        Index(
            "ix_proactive_suggestions_org_status_created_at",
            "organization_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_proactive_suggestions_org_trip_created_at",
            "organization_id",
            "trip_id",
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
    trip_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_trigger_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("proactive_triggers.id", ondelete="SET NULL"),
        index=True,
    )
    suggestion_type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="PENDING")
    title: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    proposed_action: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    requires_approval: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    severity: Mapped[str] = mapped_column(Text, nullable=False, server_default="MEDIUM")
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    push_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="PENDING")
    push_reason: Mapped[str | None] = mapped_column(Text)
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    feedback_penalty: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_result: Mapped[dict | None] = mapped_column(JSONB)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class SuggestionAudit(Base):
    __tablename__ = "suggestion_audit"
    __table_args__ = (
        CheckConstraint(
            "action IN ('DETECTED','EVALUATED','PROPOSED','SUPPRESSED','APPROVED',"
            "'EXECUTED','DISMISSED','AUDITED','LEARNED')",
            name="action_allowed",
        ),
        CheckConstraint("actor_type IN ('SYSTEM','USER')", name="actor_type_allowed"),
        Index("ix_suggestion_audit_suggestion_id_created_at", "suggestion_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    suggestion_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("proactive_suggestions.id", ondelete="CASCADE"),
        nullable=False,
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    actor_type: Mapped[str] = mapped_column(Text, nullable=False, server_default="SYSTEM")
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = created_at()


class Approval(Base):
    """通用人审记录。``subject_type`` 目前只有 ``proactive_suggestion``，但结构为后续审批矩阵预留。"""

    __tablename__ = "approvals"
    __table_args__ = (
        # 同一对象允许多个审批人；同一审批人只能有一条决定。
        Index(
            "uq_approvals_org_subject_approver",
            "organization_id",
            "subject_type",
            "subject_id",
            "decided_by_user_id",
            unique=True,
            postgresql_where=text("decided_by_user_id IS NOT NULL"),
        ),
        CheckConstraint("decision IN ('APPROVED','REJECTED')", name="decision_allowed"),
        Index("ix_approvals_subject", "subject_type", "subject_id"),
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
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


__all__ = [
    "APPROVAL_DECISIONS",
    "AUDIT_ACTIONS",
    "Approval",
    "PUSH_STATUSES",
    "ProactiveSuggestion",
    "ProactiveTrigger",
    "SEVERITIES",
    "SUGGESTION_STATUSES",
    "SuggestionAudit",
    "TRIGGER_STATUSES",
    "TRIGGER_TYPES",
]
