"""组织级汇率快照（PRD 方向六：费用估算）。

汇率不是系统"猜"出来的：每条记录都由管理员显式录入，带生效时间与来源；预算折算只使用
最新一条已录入的汇率。没有汇率的币种保持未折算，绝不按 1:1 或猜测值合并。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk


class FxRate(Base):
    """``rate`` 语义：1 单位 ``base_currency`` = ``rate`` 单位 ``quote_currency``。"""

    __tablename__ = "fx_rates"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "base_currency",
            "quote_currency",
            "as_of",
            name="uq_fx_rates_org_pair_as_of",
        ),
        CheckConstraint("base_currency <> quote_currency", name="pair_distinct"),
        CheckConstraint("rate > 0", name="rate_positive"),
        Index("ix_fx_rates_org_pair_as_of", "organization_id", "base_currency", "quote_currency", "as_of"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    base_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


__all__ = ["FxRate"]
