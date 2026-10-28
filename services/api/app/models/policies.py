"""差旅政策模型。对应迁移 ``0020_travel_policies``。

企业差旅政策不是"建议"而是可核对的规则集合。MVP+ 先支持四类可确定性判断的规则：

- 单次出行预算上限（``max_trip_budget``）；
- 计划交通费用上限（``max_plan_cost``）；
- 最少提前预订天数（``min_advance_booking_days``）；
- 允许的目的国（``allowed_countries`` 为空表示不限制）。

``strict=True`` 时，激活计划遇到违规会被拒绝（409 POLICY_VIOLATION）；否则只记录违规供人判断。
规则不引入 LLM：合规结论必须来自确定性比较。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk


class TravelPolicy(Base):
    __tablename__ = "travel_policies"
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_travel_policies_org_name"),
        CheckConstraint("max_trip_budget IS NULL OR max_trip_budget >= 0", name="trip_budget_non_negative"),
        CheckConstraint("max_plan_cost IS NULL OR max_plan_cost >= 0", name="plan_cost_non_negative"),
        CheckConstraint(
            "min_advance_booking_days IS NULL OR min_advance_booking_days >= 0",
            name="advance_days_non_negative",
        ),
        Index("ix_travel_policies_org_enabled", "organization_id", "enabled"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="CNY")
    max_trip_budget: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    max_plan_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    min_advance_booking_days: Mapped[int | None] = mapped_column(Integer)
    allowed_countries: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, server_default="{}")
    strict: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


__all__ = ["TravelPolicy"]
