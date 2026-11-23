"""差旅费用与预算汇总（PRD 方向六）。

规则：

- 费用只按录入币种归集；**不做汇率换算**。不同币种分别展示；只有与预算/计划同币种时才计算差额。
- 预算来自 `trips.budget_amount/budget_currency`；计划估算来自当前 ACTIVE 计划的
  `cost_total/cost_currency/cost_unknown_count`。
- 未知计划费用不当作 0：`planned_cost_unknown_count > 0` 时，差额只作为参考值。
- 可选 ``base_currency``：只使用组织显式录入的汇率折算；缺汇率的币种保持未折算，
  ``conversion_complete=false``，不按 1:1 或猜测值合并。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import NotFound, Unprocessable
from app.api.schemas import ExpenseCreate
from app.models.expenses import TripExpense
from app.models.planning import PlanVersion
from app.models.receipts import ReceiptAsset
from app.models.trip import Trip
from app.services import fx_service, trip_service


@dataclass
class BudgetSummary:
    budget_amount: Decimal | None
    budget_currency: str
    planned_cost_total: float | None
    planned_cost_currency: str | None
    planned_cost_unknown_count: int
    actual_by_currency: list[dict] = field(default_factory=list)
    actual_total_in_budget_currency: Decimal | None = None
    variance_vs_budget: Decimal | None = None
    variance_vs_plan: float | None = None
    conversion_base_currency: str | None = None
    converted_actual_total: Decimal | None = None
    converted_planned_cost_total: Decimal | None = None
    converted_variance_vs_budget: Decimal | None = None
    conversion_complete: bool | None = None
    unconverted_currencies: list[str] = field(default_factory=list)
    fx_conversions: list[dict] = field(default_factory=list)


def create_expense(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID | str,
    payload: ExpenseCreate,
    *,
    actor_user_id: UUID | str | None = None,
) -> TripExpense:
    trip_service.require_trip(session, organization_id, trip_id)
    if payload.receipt_asset_id is not None:
        asset = session.scalars(
            select(ReceiptAsset).where(
                ReceiptAsset.organization_id == str(organization_id),
                ReceiptAsset.trip_id == str(trip_id),
                ReceiptAsset.id == str(payload.receipt_asset_id),
                ReceiptAsset.storage_status == "AVAILABLE",
            )
        ).one_or_none()
        if asset is None:
            raise Unprocessable(
                "票据资产不存在、不可用或不属于当前出行",
                details={"receipt_asset_id": str(payload.receipt_asset_id)},
            )
    expense = TripExpense(
        organization_id=str(organization_id),
        trip_id=str(trip_id),
        category=payload.category,
        amount=payload.amount,
        currency=payload.currency.upper(),
        occurred_on=payload.occurred_on,
        note=payload.note,
        receipt_ref=payload.receipt_ref,
        receipt_asset_id=payload.receipt_asset_id,
        created_by_user_id=None if actor_user_id is None else str(actor_user_id),
    )
    session.add(expense)
    session.flush()
    return expense


def list_expenses(
    session: Session, organization_id: UUID | str, trip_id: UUID | str
) -> list[TripExpense]:
    return list(
        session.scalars(
            select(TripExpense)
            .where(
                TripExpense.organization_id == str(organization_id),
                TripExpense.trip_id == str(trip_id),
            )
            .order_by(TripExpense.occurred_on.desc(), TripExpense.created_at.desc())
        )
    )


def delete_expense(
    session: Session, organization_id: UUID | str, expense_id: UUID | str
) -> None:
    expense = session.scalars(
        select(TripExpense).where(
            TripExpense.organization_id == str(organization_id),
            TripExpense.id == str(expense_id),
        )
    ).one_or_none()
    if expense is None:
        raise NotFound("费用记录不存在", details={"expense_id": str(expense_id)})
    session.delete(expense)
    session.flush()


def budget_summary(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID | str,
    *,
    base_currency: str | None = None,
) -> BudgetSummary:
    trip: Trip = trip_service.require_trip(session, organization_id, trip_id)
    rows = session.execute(
        select(
            TripExpense.currency,
            func.coalesce(func.sum(TripExpense.amount), 0),
            func.count(),
        )
        .where(
            TripExpense.organization_id == str(organization_id),
            TripExpense.trip_id == str(trip_id),
        )
        .group_by(TripExpense.currency)
        .order_by(TripExpense.currency)
    ).all()
    actual_by_currency = [
        {"currency": row[0], "amount": float(row[1]), "count": int(row[2])}
        for row in rows
    ]
    same_currency = next(
        (row for row in rows if row[0] == trip.currency),
        None,
    )
    actual_same = Decimal(str(same_currency[1])) if same_currency is not None else (
        Decimal("0") if not rows else None
    )

    plan = session.scalars(
        select(PlanVersion).where(
            PlanVersion.organization_id == str(organization_id),
            PlanVersion.trip_id == str(trip_id),
            PlanVersion.status == "ACTIVE",
        )
    ).one_or_none()

    variance_budget = (
        (trip.budget_amount - actual_same)
        if trip.budget_amount is not None and actual_same is not None
        else None
    )
    variance_plan = (
        float(trip.budget_amount) - plan.cost_total  # Decimal→float 仅契约字段展示兼容
        if trip.budget_amount is not None
        and plan is not None
        and plan.cost_total is not None
        and (plan.cost_currency or trip.currency) == trip.currency
        else None
    )
    converted_actual: Decimal | None = None
    converted_plan: Decimal | None = None
    converted_variance: Decimal | None = None
    complete: bool | None = None
    unconverted: list[str] = []
    used: list[dict] = []
    base = base_currency.strip().upper() if base_currency else None
    if base:
        total = Decimal("0")
        actual_complete = True
        seen_currency: set[str] = set()
        for item in actual_by_currency:
            quote = fx_service.conversion_for(
                session, organization_id, base=base, currency=item["currency"]
            )
            if quote is None:
                actual_complete = False
                continue
            if quote.currency not in seen_currency:
                seen_currency.add(quote.currency)
                used.append(
                    {
                        "currency": quote.currency,
                        "rate": float(quote.rate),
                        "as_of": quote.as_of,
                        "source": quote.source,
                        "via_inverse": quote.via_inverse,
                    }
                )
            total += Decimal(str(item["amount"])) * quote.rate
        converted_actual = (
            total.quantize(Decimal("0.01")) if actual_complete else None
        )

        plan_complete = True
        if plan is not None:
            if plan.cost_total is None or not plan.cost_currency:
                plan_complete = False
            else:
                quote = fx_service.conversion_for(
                    session, organization_id, base=base, currency=plan.cost_currency
                )
                if quote is None:
                    plan_complete = False
                else:
                    converted_plan = (
                        Decimal(str(plan.cost_total)) * quote.rate
                    ).quantize(Decimal("0.01"))
                    if quote.currency not in seen_currency:
                        seen_currency.add(quote.currency)
                        used.append(
                            {
                                "currency": quote.currency,
                                "rate": float(quote.rate),
                                "as_of": quote.as_of,
                                "source": quote.source,
                                "via_inverse": quote.via_inverse,
                            }
                        )
            if plan.cost_unknown_count > 0:
                plan_complete = False

        complete = actual_complete and plan_complete
        unconverted = sorted(
            {
                item["currency"]
                for item in actual_by_currency
                if fx_service.conversion_for(
                    session, organization_id, base=base, currency=item["currency"]
                )
                is None
            }
            | (
                {plan.cost_currency}
                if plan is not None
                and plan.cost_currency
                and fx_service.conversion_for(
                    session, organization_id, base=base, currency=plan.cost_currency
                )
                is None
                else set()
            )
        )
        if (
            trip.budget_amount is not None
            and trip.currency == base
            and converted_actual is not None
        ):
            converted_variance = trip.budget_amount - converted_actual

    return BudgetSummary(
        budget_amount=trip.budget_amount,
        budget_currency=trip.currency,
        planned_cost_total=plan.cost_total if plan is not None else None,
        planned_cost_currency=plan.cost_currency if plan is not None else None,
        planned_cost_unknown_count=plan.cost_unknown_count if plan is not None else 0,
        actual_by_currency=actual_by_currency,
        actual_total_in_budget_currency=actual_same,
        variance_vs_budget=variance_budget,
        variance_vs_plan=variance_plan,
        conversion_base_currency=base,
        converted_actual_total=converted_actual,
        converted_planned_cost_total=converted_plan,
        converted_variance_vs_budget=converted_variance,
        conversion_complete=complete,
        unconverted_currencies=unconverted,
        fx_conversions=used,
    )


__all__ = [
    "BudgetSummary",
    "budget_summary",
    "create_expense",
    "delete_expense",
    "list_expenses",
]
