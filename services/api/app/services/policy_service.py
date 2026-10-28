"""差旅政策服务：确定性规则评估（PRD 方向六 / 企业协作）。

规则只做可核对比较，不经过 LLM：

- 预算：同币种才比较；币种不同或缺失都显式标记，不猜汇率/不默认合规。
- 计划费用：未知费用段存在时不能判合规。
- 提前预订天数。
- 允许目的国。

计划生成时把违规快照写入 `policy_violations`；激活时若政策 ``strict`` 则拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound
from app.api.schemas import TravelPolicyCreate, TravelPolicyUpdate
from app.models.policies import TravelPolicy
from app.models.trip import Trip


@dataclass
class PolicyViolation:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class PolicyEvaluation:
    policy_id: UUID | None
    policy_name: str | None
    strict: bool
    violations: list[PolicyViolation]
    checked_at: datetime


def active_policy(session: Session, organization_id: UUID | str) -> TravelPolicy | None:
    return session.scalars(
        select(TravelPolicy)
        .where(
            TravelPolicy.organization_id == str(organization_id),
            TravelPolicy.enabled.is_(True),
        )
        .order_by(TravelPolicy.created_at.desc())
        .limit(1)
    ).first()


def list_policies(
    session: Session, organization_id: UUID | str
) -> list[TravelPolicy]:
    return list(
        session.scalars(
            select(TravelPolicy)
            .where(TravelPolicy.organization_id == str(organization_id))
            .order_by(TravelPolicy.created_at.desc())
        )
    )


def require_policy(
    session: Session, organization_id: UUID | str, policy_id: UUID | str
) -> TravelPolicy:
    row = session.scalars(
        select(TravelPolicy).where(
            TravelPolicy.organization_id == str(organization_id),
            TravelPolicy.id == str(policy_id),
        )
    ).one_or_none()
    if row is None:
        raise NotFound("差旅政策不存在", details={"policy_id": str(policy_id)})
    return row


def create_policy(
    session: Session, organization_id: UUID | str, payload: TravelPolicyCreate
) -> TravelPolicy:
    row = TravelPolicy(
        organization_id=str(organization_id),
        name=payload.name.strip(),
        currency=payload.currency.upper(),
        max_trip_budget=payload.max_trip_budget,
        max_plan_cost=payload.max_plan_cost,
        min_advance_booking_days=payload.min_advance_booking_days,
        allowed_countries=[code.upper() for code in payload.allowed_countries],
        strict=payload.strict,
        enabled=payload.enabled,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError as exc:
        raise Conflict("同名差旅政策已存在", details={"name": payload.name}) from exc
    return row


def update_policy(
    session: Session,
    organization_id: UUID | str,
    policy_id: UUID | str,
    payload: TravelPolicyUpdate,
) -> TravelPolicy:
    row = require_policy(session, organization_id, policy_id)
    fields = payload.model_dump(exclude_unset=True)
    if "currency" in fields and fields["currency"]:
        fields["currency"] = fields["currency"].upper()
    if "allowed_countries" in fields and fields["allowed_countries"] is not None:
        fields["allowed_countries"] = [code.upper() for code in fields["allowed_countries"]]
    for key, value in fields.items():
        setattr(row, key, value)
    session.flush()
    return row


def delete_policy(
    session: Session, organization_id: UUID | str, policy_id: UUID | str
) -> None:
    row = require_policy(session, organization_id, policy_id)
    session.delete(row)
    session.flush()


def evaluate(
    session: Session,
    organization_id: UUID | str,
    trip: Trip,
    *,
    cost_total: float | None,
    cost_currency: str | None,
    cost_unknown_count: int,
    now: datetime | None = None,
) -> PolicyEvaluation:
    current = now or session.scalar(select(func.clock_timestamp()))
    policy = active_policy(session, organization_id)
    if policy is None:
        return PolicyEvaluation(
            policy_id=None,
            policy_name=None,
            strict=False,
            violations=[],
            checked_at=current,
        )

    violations: list[PolicyViolation] = []
    if policy.max_trip_budget is not None:
        if trip.budget_amount is None:
            violations.append(
                PolicyViolation("BUDGET_MISSING", "出行未设置预算，政策无法核验")
            )
        elif trip.currency != policy.currency:
            violations.append(
                PolicyViolation(
                    "CURRENCY_MISMATCH",
                    "出行预算币种与政策币种不同，无法直接比较",
                    {"trip_currency": trip.currency, "policy_currency": policy.currency},
                )
            )
        elif Decimal(trip.budget_amount) > policy.max_trip_budget:
            violations.append(
                PolicyViolation(
                    "BUDGET_EXCEEDED",
                    "出行预算超过政策上限",
                    {
                        "budget": float(trip.budget_amount),
                        "max": float(policy.max_trip_budget),
                        "currency": trip.currency,
                    },
                )
            )

    if policy.max_plan_cost is not None:
        if cost_unknown_count > 0:
            violations.append(
                PolicyViolation(
                    "PLAN_COST_UNKNOWN",
                    "计划存在未知费用段，无法确认是否超过政策上限",
                    {"unknown_segments": cost_unknown_count},
                )
            )
        elif cost_total is None:
            violations.append(
                PolicyViolation("PLAN_COST_MISSING", "计划没有费用数据，政策无法核验")
            )
        elif cost_currency != policy.currency:
            violations.append(
                PolicyViolation(
                    "CURRENCY_MISMATCH",
                    "计划费用币种与政策币种不同，无法直接比较",
                    {"plan_currency": cost_currency, "policy_currency": policy.currency},
                )
            )
        elif cost_total > float(policy.max_plan_cost):
            violations.append(
                PolicyViolation(
                    "PLAN_COST_EXCEEDED",
                    "计划费用超过政策上限",
                    {
                        "cost": cost_total,
                        "max": float(policy.max_plan_cost),
                        "currency": cost_currency,
                    },
                )
            )

    if policy.min_advance_booking_days is not None:
        days = (trip.window_start.date() - current.date()).days
        if days < policy.min_advance_booking_days:
            violations.append(
                PolicyViolation(
                    "ADVANCE_BOOKING_TOO_SHORT",
                    "距离出发日期不足政策要求的提前预订天数",
                    {"days_until_departure": days, "minimum": policy.min_advance_booking_days},
                )
            )

    if policy.allowed_countries:
        allowed = {code.upper() for code in policy.allowed_countries}
        disallowed = sorted({code.upper() for code in trip.countries or []} - allowed)
        if disallowed:
            violations.append(
                PolicyViolation(
                    "COUNTRY_NOT_ALLOWED",
                    "出行包含政策未允许的目的国",
                    {"countries": disallowed, "allowed": sorted(allowed)},
                )
            )

    return PolicyEvaluation(
        policy_id=policy.id,
        policy_name=policy.name,
        strict=policy.strict,
        violations=violations,
        checked_at=current,
    )


def evaluation_payload(evaluation: PolicyEvaluation) -> list[dict[str, Any]]:
    return [
        {"code": item.code, "message": item.message, "details": item.details}
        for item in evaluation.violations
    ]


__all__ = [
    "PolicyEvaluation",
    "PolicyViolation",
    "active_policy",
    "create_policy",
    "delete_policy",
    "evaluate",
    "evaluation_payload",
    "list_policies",
    "require_policy",
    "update_policy",
]
