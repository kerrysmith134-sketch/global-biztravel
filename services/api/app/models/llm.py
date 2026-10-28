"""LLM 调用审计模型。对应迁移 ``0008_llm_invocations``。

设计文档 v2.0 第 7.2 章要求：每条 Agent 产出（解释、建议理由、草拟文本）都必须能回溯到一条
``llm_invocation``。本表就是那条回溯链的落点，字段与设计文档保持一致，另加两个运维必需列：

- ``cached``：命中重放缓存。缓存命中仍然记一条调用记录，因为"这次请求发生过"本身是审计事实；
  但 ``tokens_*`` 与 ``latency_ms`` 记 0，避免把缓存命中算成模型成本。
- ``error``：降级原因。``degraded=true`` 时必须能回答"为什么降级"，否则降级会变成静默失败。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, uuid_pk


class LLMInvocation(Base):
    __tablename__ = "llm_invocations"
    __table_args__ = (
        CheckConstraint("tokens_in >= 0", name="tokens_in_non_negative"),
        CheckConstraint("tokens_out >= 0", name="tokens_out_non_negative"),
        CheckConstraint("latency_ms >= 0", name="latency_non_negative"),
        Index("ix_llm_invocations_org_task_created_at", "organization_id", "task", "created_at"),
        Index("ix_llm_invocations_org_input_hash", "organization_id", "input_hash"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    task: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_template_id: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    input_hash: Mapped[str] = mapped_column(Text, nullable=False)
    output_json: Mapped[dict | None] = mapped_column(JSONB)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cost_estimate: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, server_default="0"
    )
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    error: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


__all__ = ["LLMInvocation"]
