"""报销单模型。对应迁移 ``0022_expense_reports``。

费用记录只是原始凭证；报销单是提交给审批人的**不可变快照**。因此：

- ``expense_report_items`` 保存建单时的类别/金额/币种/日期/备注/凭证引用；
- 之后费用记录被修改/删除，不影响已提交报销单的内容；
- 报销单状态：DRAFT → SUBMITTED → APPROVED / REJECTED；
- 审批落 ``approvals``（subject_type=expense_report）与 ``workflow_instances``。

多币种仍不换算：报销单按币种分组展示金额。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk

REPORT_STATUSES = ("DRAFT", "SUBMITTED", "APPROVED", "REJECTED")


class ExpenseReport(Base):
    __tablename__ = "expense_reports"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED')", name="status_allowed"
        ),
        Index("ix_expense_reports_org_trip", "organization_id", "trip_id"),
        Index("ix_expense_reports_org_status", "organization_id", "status"),
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
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="DRAFT")
    notes: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class ExpenseReportItem(Base):
    __tablename__ = "expense_report_items"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "category IN ('TRANSPORT','LODGING','MEALS','OTHER')", name="category_allowed"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("expense_reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expense_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trip_expenses.id", ondelete="SET NULL")
    )
    category: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    receipt_ref: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


__all__ = ["ExpenseReport", "ExpenseReportItem", "REPORT_STATUSES"]
