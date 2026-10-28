"""审批策略模型。对应迁移 ``0013_approval_policies``。

设计文档 N1 要求为"企业多级审批"预留抽象。本实现先用**单级策略**解决真实问题：不同风险等级
的主动建议需要不同最低角色才能批准。默认没有任何策略 ⇒ 行为与之前一致（有 TRAVELER 即可批准），
因此不会破坏既有流程。

``min_severity`` 是策略适用的最低风险等级（LOW 覆盖全部；HIGH 只覆盖 HIGH）。``required_role``
是批准所需的最低角色。多级/多人会签不在本次范围，表结构保留 ``subject_type`` 以便后续扩展。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, uuid_pk

ROLES = ("VIEWER", "TRAVELER", "ADMIN", "OWNER")
SEVERITIES = ("LOW", "MEDIUM", "HIGH")


class ApprovalPolicy(Base):
    __tablename__ = "approval_policies"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "subject_type",
            "min_severity",
            name="uq_approval_policies_org_subject_severity",
        ),
        CheckConstraint(
            "required_role IN ('VIEWER','TRAVELER','ADMIN','OWNER')", name="role_allowed"
        ),
        CheckConstraint(
            "min_severity IN ('LOW','MEDIUM','HIGH')", name="severity_allowed"
        ),
        CheckConstraint("required_approvals >= 1", name="required_approvals_positive"),
        Index("ix_approval_policies_org_enabled", "organization_id", "enabled"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subject_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="proactive_suggestion"
    )
    min_severity: Mapped[str] = mapped_column(Text, nullable=False, server_default="LOW")
    required_role: Mapped[str] = mapped_column(Text, nullable=False, server_default="TRAVELER")
    required_approvals: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = created_at()


__all__ = ["ApprovalPolicy", "ROLES", "SEVERITIES"]
