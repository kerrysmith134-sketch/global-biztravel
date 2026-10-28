"""指标快照模型。对应迁移 ``0009_metric_snapshots``。

设计文档 v2.0 第 16 章：指标不是"看板上的一个数"，而是可留存、可对比的时间序列。
保存快照的原因有三：

1. **周期对比**：DLQ 率、降级率这类比率必须按窗口留存，否则只能看"此刻"。
2. **回归检测**：发布前后同一指标的变化要能追溯。
3. **审计**：SLO 达标与否需要证据，而不是只有一句"当时看着正常"。

表结构刻意做成 key-value + 窗口，而不是每指标一列：新增指标只改服务，不改表。
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
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, uuid_pk


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "metric_key",
            "window_start",
            "window_end",
            name="uq_metric_snapshots_org_key_window",
        ),
        CheckConstraint("sample_size >= 0", name="sample_size_non_negative"),
        Index("ix_metric_snapshots_org_key_created_at", "organization_id", "metric_key", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    metric_key: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False, server_default="0")
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    dimensions: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = created_at()


__all__ = ["MetricSnapshot"]
