"""外部信号模型。对应迁移 ``0011_external_signals``。

设计文档 v2.0 第 14 章的 EXTERNAL 触发源需要一个**可审计的输入落点**：签证政策变更、天气
中断、汇率波动、航班状态都不是本系统产生的，但主动式运行时必须能消费它们。本表只存
"谁在什么时间、对什么对象、报告了什么"，不存推断结论。

几个刻意的字段：

- ``provider`` + ``dedupe_key`` 唯一：同一个外部源重复推送同一条信号只落一行（``upsert``）。
- ``subject_type`` / ``subject_ref``：信号可以指向国家（``COUNTRY``）、某次出行（``TRIP``）
  或客户（``CUSTOMER``），主动式扫描时按出行上下文匹配。
- ``observed_at`` / ``valid_until``：区分"外部源观察到的时间"与"这条信号何时失效"。
- ``confidence``：由上报方给出，主动式建议会沿用它并接受误报回收的折扣。
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
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk

SIGNAL_TYPES = (
    "VISA_POLICY_CHANGE",
    "WEATHER_DISRUPTION",
    "FX_VOLATILITY",
    "FLIGHT_STATUS",
)
SUBJECT_TYPES = ("COUNTRY", "TRIP", "CUSTOMER")


class ExternalSignal(Base):
    __tablename__ = "external_signals"
    __table_args__ = (
        UniqueConstraint(
            "organization_id", "provider", "dedupe_key", name="uq_external_signals_org_provider_dedupe"
        ),
        CheckConstraint(
            "signal_type IN ('VISA_POLICY_CHANGE','WEATHER_DISRUPTION','FX_VOLATILITY','FLIGHT_STATUS')",
            name="signal_type_allowed",
        ),
        CheckConstraint(
            "subject_type IN ('COUNTRY','TRIP','CUSTOMER')", name="subject_type_allowed"
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        Index("ix_external_signals_org_subject", "organization_id", "subject_type", "subject_ref"),
        Index("ix_external_signals_org_signal_type", "organization_id", "signal_type"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    signal_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_type: Mapped[str] = mapped_column(Text, nullable=False)
    subject_ref: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, server_default="0.7")
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


__all__ = ["ExternalSignal", "SIGNAL_TYPES", "SUBJECT_TYPES"]
