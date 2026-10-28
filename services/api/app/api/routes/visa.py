"""签证案件与入境评估接口（PRD 方向二）。"""

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
    EntryAssessmentRead,
    VisaCaseCreate,
    VisaCaseRead,
    VisaCaseUpdate,
)
from app.services import trip_service, visa_service

router = APIRouter(tags=["visa"])


@router.get(
    "/trips/{trip_id}/visa-cases",
    response_model=list[VisaCaseRead],
    summary="出行的签证案件列表",
)
def list_visa_cases(trip_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    trip_service.require_trip(session, context.organization_id, trip_id)
    return [
        VisaCaseRead.model_validate(case)
        for case in visa_service.list_cases(session, context.organization_id, trip_id)
    ]


@router.post(
    "/trips/{trip_id}/visa-cases",
    response_model=list[VisaCaseRead],
    status_code=status.HTTP_201_CREATED,
    summary="按目的国创建签证案件（幂等；缺省用出行国家）",
)
def create_visa_cases(
    trip_id: UUID,
    payload: VisaCaseCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint="POST /v1/trips/{trip_id}/visa-cases",
        key=idempotency_key,
        payload={"trip_id": str(trip_id), "countries": payload.countries},
    )
    if (replay := guard.begin()) is not None:
        return replay
    trip = trip_service.require_trip(session, context.organization_id, trip_id)
    cases = visa_service.ensure_cases(
        session,
        context.organization_id,
        trip,
        countries=payload.countries,
        actor_user_id=context.user_id,
    )
    body = [VisaCaseRead.model_validate(case) for case in cases]
    return guard.finish(status_code=status.HTTP_201_CREATED, body=body)


@router.patch(
    "/visa-cases/{case_id}",
    response_model=VisaCaseRead,
    summary="更新签证案件状态/清单/备注",
)
def update_visa_case(
    case_id: UUID,
    payload: VisaCaseUpdate,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_TRAVELER)
    case = visa_service.update_case(
        session,
        context.organization_id,
        case_id,
        status=payload.status,
        notes=payload.notes,
        checklist=(
            [item.model_dump() for item in payload.checklist]
            if payload.checklist is not None
            else None
        ),
        passport_expires_at=payload.passport_expires_at,
    )
    return VisaCaseRead.model_validate(case)


@router.get(
    "/trips/{trip_id}/entry-assessments",
    response_model=list[EntryAssessmentRead],
    summary="出行的入境要求评估快照",
)
def list_entry_assessments(trip_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    trip_service.require_trip(session, context.organization_id, trip_id)
    return [
        EntryAssessmentRead.model_validate(item)
        for item in visa_service.list_assessments(session, context.organization_id, trip_id)
    ]


@router.post(
    "/trips/{trip_id}/entry-assessments:refresh",
    response_model=list[EntryAssessmentRead],
    summary="刷新入境要求评估（离线目录，标注需人工复核）",
)
def refresh_entry_assessments(trip_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_TRAVELER)
    trip = trip_service.require_trip(session, context.organization_id, trip_id)
    rows = visa_service.assess_entry(session, context.organization_id, trip)
    return [EntryAssessmentRead.model_validate(item) for item in rows]
