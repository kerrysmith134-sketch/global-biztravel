"""Trip-level Risk & Recovery API.

This surface exposes the consolidated case lifecycle. Source domains remain authoritative;
all state changes are versioned and appended to trip_risk_case_events.
"""
from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import (
    ROLE_TRAVELER,
    ROLE_VIEWER,
    ContextDep,
    IdempotencyKeyDep,
    SessionDep,
    require_role,
)
from app.api.idempotent import IdempotentWrite
from app.api.pagination import clamp_limit
from app.api.schemas import (
    Page,
    TripRiskCaseEventRead,
    TripRiskCaseRead,
    TripRiskReconciliationRead,
    TripRiskStatus,
    TripRiskTransitionRead,
    TripRiskTransitionRequest,
)
from app.services import trip_risk_service, trip_service

router = APIRouter(tags=["trip-risks"])

RECONCILE_ENDPOINT = "POST /v1/trips/{trip_id}/risk-cases:reconcile"
TRANSITION_ENDPOINT = "POST /v1/trips/{trip_id}/risk-cases/{case_id}:transition"


@router.get(
    "/trips/{trip_id}/risk-cases",
    response_model=Page[TripRiskCaseRead],
    summary="出行级风险与恢复事项（统一跨源视图）",
)
def list_trip_risk_cases(
    trip_id: UUID,
    context: ContextDep,
    session: SessionDep,
    case_status: Annotated[TripRiskStatus | None, Query(alias="status")] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
):
    require_role(context, ROLE_VIEWER)
    trip_service.require_trip(session, context.organization_id, trip_id)
    page_size = clamp_limit(limit, default=50, maximum=200)
    items = trip_risk_service.list_cases(
        session,
        context.organization_id,
        trip_id,
        status=case_status,
        limit=page_size,
    )
    return Page[TripRiskCaseRead](
        items=[TripRiskCaseRead.model_validate(item) for item in items],
        next_cursor=None,
    )


@router.get(
    "/trips/{trip_id}/risk-cases/{case_id}/events",
    response_model=list[TripRiskCaseEventRead],
    summary="风险与恢复状态事件时间线",
)
def list_trip_risk_case_events(
    trip_id: UUID,
    case_id: UUID,
    context: ContextDep,
    session: SessionDep,
    limit: Annotated[int | None, Query(ge=1)] = None,
):
    require_role(context, ROLE_VIEWER)
    trip_service.require_trip(session, context.organization_id, trip_id)
    rows = trip_risk_service.list_case_events(
        session,
        context.organization_id,
        trip_id,
        case_id,
        limit=limit or 100,
    )
    return [TripRiskCaseEventRead.model_validate(row) for row in rows]


@router.post(
    "/trips/{trip_id}/risk-cases:reconcile",
    response_model=TripRiskReconciliationRead,
    status_code=status.HTTP_200_OK,
    summary="从主动建议和人工跟进重建统一风险视图",
)
def reconcile_trip_risk_cases(
    trip_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=RECONCILE_ENDPOINT,
        key=idempotency_key,
        payload={"trip_id": str(trip_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay

    report = trip_risk_service.reconcile_trip(
        session,
        context.organization_id,
        trip_id,
    )
    body = TripRiskReconciliationRead(
        trip_id=trip_id,
        cases_created=report.cases_created,
        cases_updated=report.cases_updated,
        cases_reopened=report.cases_reopened,
        escalated=report.escalated,
        reviewed=report.reviewed,
        errors=report.errors,
    )
    return guard.finish(status_code=status.HTTP_200_OK, body=body)


@router.post(
    "/trips/{trip_id}/risk-cases/{case_id}:transition",
    response_model=TripRiskTransitionRead,
    status_code=status.HTTP_200_OK,
    summary="人工确认、开始处理、等待外部或关闭风险事项",
)
def transition_trip_risk_case(
    trip_id: UUID,
    case_id: UUID,
    payload: TripRiskTransitionRequest,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=TRANSITION_ENDPOINT,
        key=idempotency_key,
        payload={
            "trip_id": str(trip_id),
            "case_id": str(case_id),
            **payload.model_dump(mode="json"),
        },
    )
    if (replay := guard.begin()) is not None:
        return replay

    outcome = trip_risk_service.transition_case(
        session,
        context.organization_id,
        trip_id,
        case_id,
        expected_version=payload.expected_version,
        to_status=payload.to_status,
        actor_user_id=context.user_id,
        note=payload.note,
        next_review_at=payload.next_review_at,
        resolution_basis=payload.resolution_basis,
    )
    body = TripRiskTransitionRead(
        case=TripRiskCaseRead.model_validate(outcome.case),
        event=TripRiskCaseEventRead.model_validate(outcome.event),
    )
    return guard.finish(status_code=status.HTTP_200_OK, body=body)


__all__ = ["router"]
