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
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
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
        UniqueConstraint(
            "organization_id", "trip_id", "id", name="uq_expense_reports_org_trip_id"
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
        CheckConstraint(
            "("
            "receipt_asset_id IS NULL AND receipt_sha256 IS NULL "
            "AND receipt_original_filename IS NULL AND receipt_mime_type IS NULL "
            "AND receipt_byte_length IS NULL"
            ") OR ("
            "receipt_asset_id IS NOT NULL AND length(receipt_sha256) = 64 "
            "AND length(btrim(receipt_original_filename)) > 0 "
            "AND length(btrim(receipt_mime_type)) > 0 AND receipt_byte_length > 0"
            ")",
            name="receipt_snapshot_metadata_consistent",
        ),
        ForeignKeyConstraint(
            ["organization_id", "trip_id", "report_id"],
            ["expense_reports.organization_id", "expense_reports.trip_id", "expense_reports.id"],
            ondelete="CASCADE",
            name="fk_expense_report_items_report_same_trip",
        ),
        ForeignKeyConstraint(
            ["organization_id", "trip_id", "receipt_asset_id"],
            ["receipt_assets.organization_id", "receipt_assets.trip_id", "receipt_assets.id"],
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
            name="fk_expense_report_items_receipt_asset_same_trip",
        ),
        Index("ix_expense_report_items_receipt_asset_id", "receipt_asset_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    report_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    expense_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trip_expenses.id", ondelete="SET NULL")
    )
    category: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    receipt_ref: Mapped[str | None] = mapped_column(Text)
    receipt_asset_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    receipt_sha256: Mapped[str | None] = mapped_column(Text)
    receipt_original_filename: Mapped[str | None] = mapped_column(Text)
    receipt_mime_type: Mapped[str | None] = mapped_column(Text)
    receipt_byte_length: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at()


__all__ = ["ExpenseReport", "ExpenseReportItem", "REPORT_STATUSES"]
