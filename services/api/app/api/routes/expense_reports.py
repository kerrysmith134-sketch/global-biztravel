"""报销单接口（PRD 方向六）。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, status

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_TRAVELER,
    ROLE_VIEWER,
    ContextDep,
    IdempotencyKeyDep,
    SessionDep,
    require_role,
)
from app.api.idempotent import IdempotentWrite
from app.api.schemas import (
    CurrencyTotalRead,
    ExpenseReportCreate,
    ExpenseReportDecisionRequest,
    ExpenseReportItemRead,
    ExpenseReportRead,
)
from app.services import expense_report_service, trip_service

router = APIRouter(tags=["expense-reports"])


def _read(detail) -> ExpenseReportRead:  # noqa: ANN001
    report = detail.report
    return ExpenseReportRead(
        id=report.id,
        trip_id=report.trip_id,
        status=report.status,
        notes=report.notes,
        submitted_at=report.submitted_at,
        decided_at=report.decided_at,
        decided_by_user_id=report.decided_by_user_id,
        cancelled_at=report.cancelled_at,
        cancelled_by_user_id=report.cancelled_by_user_id,
        cancellation_reason=report.cancellation_reason,
        items=[ExpenseReportItemRead.model_validate(item) for item in detail.items],
        totals=[CurrencyTotalRead(**item) for item in detail.totals],
        created_at=report.created_at,
        updated_at=report.updated_at,
    )


@router.get(
    "/trips/{trip_id}/expense-reports",
    response_model=list[ExpenseReportRead],
    summary="出行的报销单列表",
)
def list_reports(trip_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    trip_service.require_trip(session, context.organization_id, trip_id)
    return [
        _read(expense_report_service.get_detail(session, context.organization_id, row.id))
        for row in expense_report_service.list_reports(
            session, context.organization_id, trip_id
        )
    ]


@router.post(
    "/trips/{trip_id}/expense-reports",
    response_model=ExpenseReportRead,
    status_code=status.HTTP_201_CREATED,
    summary="从费用记录创建报销单快照",
)
def create_report(
    trip_id: UUID,
    payload: ExpenseReportCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint="POST /v1/trips/{trip_id}/expense-reports",
        key=idempotency_key,
        payload={"trip_id": str(trip_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    detail = expense_report_service.create_report(
        session,
        context.organization_id,
        trip_id,
        payload,
        actor_user_id=context.user_id,
    )
    return guard.finish(status_code=status.HTTP_201_CREATED, body=_read(detail))


@router.get(
    "/expense-reports/{report_id}",
    response_model=ExpenseReportRead,
    summary="报销单详情（含快照明细与分币种合计）",
)
def get_report(report_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    return _read(
        expense_report_service.get_detail(session, context.organization_id, report_id)
    )


@router.post(
    "/expense-reports/{report_id}:submit",
    response_model=ExpenseReportRead,
    summary="提交报销单（DRAFT → SUBMITTED）",
)
def submit_report(
    report_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint="POST /v1/expense-reports/{report_id}:submit",
        key=idempotency_key,
        payload={"report_id": str(report_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay
    detail = expense_report_service.submit_report(
        session, context.organization_id, report_id, actor_user_id=context.user_id
    )
    return guard.finish(status_code=status.HTTP_200_OK, body=_read(detail))


@router.post(
    "/expense-reports/{report_id}:cancel",
    response_model=ExpenseReportRead,
    summary="撤销草稿报销单（仅 DRAFT；保留费用快照）",
)
def cancel_report(
    report_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint="POST /v1/expense-reports/{report_id}:cancel",
        key=idempotency_key,
        payload={"report_id": str(report_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay
    detail = expense_report_service.cancel_report(
        session,
        context.organization_id,
        report_id,
        actor_user_id=context.user_id,
        can_cancel_any=context.rank >= ROLE_ADMIN,
    )
    return guard.finish(status_code=status.HTTP_200_OK, body=_read(detail))


@router.post(
    "/expense-reports/{report_id}:approve",
    response_model=ExpenseReportRead,
    summary="审批通过报销单（ADMIN）",
)
def approve_report(
    report_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    payload: Annotated[ExpenseReportDecisionRequest | None, Body()] = None,
):
    return _decide(
        report_id,
        context,
        session,
        idempotency_key,
        approve=True,
        note=payload.note if payload is not None else None,
    )


@router.post(
    "/expense-reports/{report_id}:reject",
    response_model=ExpenseReportRead,
    summary="驳回报销单（ADMIN）",
)
def reject_report(
    report_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    payload: Annotated[ExpenseReportDecisionRequest | None, Body()] = None,
):
    return _decide(
        report_id,
        context,
        session,
        idempotency_key,
        approve=False,
        note=payload.note if payload is not None else None,
    )


def _decide(
    report_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: str | None,
    *,
    approve: bool,
    note: str | None,
):
    require_role(context, ROLE_ADMIN)
    endpoint = (
        "POST /v1/expense-reports/{report_id}:approve"
        if approve
        else "POST /v1/expense-reports/{report_id}:reject"
    )
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"report_id": str(report_id), "note": note},
    )
    if (replay := guard.begin()) is not None:
        return replay
    detail = expense_report_service.decide_report(
        session,
        context.organization_id,
        report_id,
        approve=approve,
        actor_user_id=context.user_id,
        note=note,
    )
    return guard.finish(status_code=status.HTTP_200_OK, body=_read(detail))
