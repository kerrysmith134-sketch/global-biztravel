"""CRM 同步接口（PRD 访后跟进）。"""

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
from app.api.schemas import CrmSyncJobRead
from app.services import crm_service, trip_service

router = APIRouter(tags=["crm"])


@router.post(
    "/visit-records/{record_id}/crm-sync",
    response_model=CrmSyncJobRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="为一条拜访记录创建 CRM 同步任务",
)
def enqueue_crm_sync(
    record_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint="POST /v1/visit-records/{record_id}/crm-sync",
        key=idempotency_key,
        payload={"visit_record_id": str(record_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay
    crm_job = crm_service.enqueue_sync(
        session, context.organization_id, record_id, actor_user_id=context.user_id
    )
    return guard.finish(
        status_code=status.HTTP_202_ACCEPTED,
        body=CrmSyncJobRead.model_validate(crm_job),
    )


@router.get(
    "/crm-sync-jobs/{crm_job_id}",
    response_model=CrmSyncJobRead,
    summary="CRM 同步任务状态",
)
def get_crm_sync_job(crm_job_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    return CrmSyncJobRead.model_validate(
        crm_service.get_job(session, context.organization_id, crm_job_id)
    )


@router.get(
    "/trips/{trip_id}/crm-sync-jobs",
    response_model=list[CrmSyncJobRead],
    summary="出行的 CRM 同步任务列表",
)
def list_crm_sync_jobs(trip_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    trip_service.require_trip(session, context.organization_id, trip_id)
    return [
        CrmSyncJobRead.model_validate(row)
        for row in crm_service.list_jobs(session, context.organization_id, trip_id)
    ]
