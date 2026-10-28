"""差旅费用与预算接口（PRD 方向六）。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import (
    ROLE_TRAVELER,
    ROLE_VIEWER,
    ContextDep,
    IdempotencyKeyDep,
    SessionDep,
    require_role,
)
from app.api.idempotent import IdempotentWrite
from app.api.schemas import (
    BudgetSummaryRead,
    CurrencyTotalRead,
    ExpenseCreate,
    ExpenseRead,
)
from app.services import expense_service, trip_service

router = APIRouter(tags=["expenses"])


@router.get(
    "/trips/{trip_id}/expenses",
    response_model=list[ExpenseRead],
    summary="出行的费用记录",
)
def list_expenses(trip_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    trip_service.require_trip(session, context.organization_id, trip_id)
    return [
        ExpenseRead.model_validate(row)
        for row in expense_service.list_expenses(session, context.organization_id, trip_id)
    ]


@router.post(
    "/trips/{trip_id}/expenses",
    response_model=ExpenseRead,
    status_code=status.HTTP_201_CREATED,
    summary="录入一笔费用",
)
def create_expense(
    trip_id: UUID,
    payload: ExpenseCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint="POST /v1/trips/{trip_id}/expenses",
        key=idempotency_key,
        payload={"trip_id": str(trip_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = expense_service.create_expense(
        session,
        context.organization_id,
        trip_id,
        payload,
        actor_user_id=context.user_id,
    )
    return guard.finish(status_code=status.HTTP_201_CREATED, body=ExpenseRead.model_validate(row))


@router.delete(
    "/expenses/{expense_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除费用记录",
)
def delete_expense(expense_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_TRAVELER)
    expense_service.delete_expense(session, context.organization_id, expense_id)


@router.get(
    "/trips/{trip_id}/budget",
    response_model=BudgetSummaryRead,
    summary="预算、计划估算与实际费用（多币种分列，不伪造汇率）",
)
def get_budget(trip_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    summary = expense_service.budget_summary(session, context.organization_id, trip_id)
    return BudgetSummaryRead(
        budget_amount=summary.budget_amount,
        budget_currency=summary.budget_currency,
        planned_cost_total=summary.planned_cost_total,
        planned_cost_currency=summary.planned_cost_currency,
        planned_cost_unknown_count=summary.planned_cost_unknown_count,
        actual_by_currency=[CurrencyTotalRead(**item) for item in summary.actual_by_currency],
        actual_total_in_budget_currency=summary.actual_total_in_budget_currency,
        variance_vs_budget=summary.variance_vs_budget,
        variance_vs_plan=summary.variance_vs_plan,
    )
