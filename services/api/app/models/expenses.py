"""差旅费用模型。对应迁移 ``0019_trip_expenses``。

PRD「方向六：AI 差旅费用与财务闭环」的第一层：先把一次出行的费用按币种归集起来，和预算、
计划估算对比。MVP 阶段**不做假汇率换算**：不同币种分别汇总，只对同币种计算差额；换算需要
真实汇率源，宁可不合并，也不把未知汇率当 1。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, uuid_pk

EXPENSE_CATEGORIES = ("TRANSPORT", "LODGING", "MEALS", "OTHER")


class TripExpense(Base):
    __tablename__ = "trip_expenses"
    __table_args__ = (
        CheckConstraint(
            "category IN ('TRANSPORT','LODGING','MEALS','OTHER')", name="category_allowed"
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        Index("ix_trip_expenses_org_trip_date", "organization_id", "trip_id", "occurred_on"),
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
    category: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    receipt_ref: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()


__all__ = ["EXPENSE_CATEGORIES", "TripExpense"]
