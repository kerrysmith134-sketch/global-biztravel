"""按 Trip 的审计时间线（设计文档第 15 章）。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import ROLE_VIEWER, ContextDep, SessionDep, require_role
from app.api.schemas import AuditEventRead
from app.services import audit_service, trip_service

router = APIRouter(tags=["audit"])


@router.get(
    "/trips/{trip_id}/audit",
    response_model=list[AuditEventRead],
    summary="一次出行的审计时间线（建议/审批/Outbox/记录/规划尝试）",
)
def trip_audit(
    trip_id: UUID,
    context: ContextDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
):
    require_role(context, ROLE_VIEWER)
    trip_service.require_trip(session, context.organization_id, trip_id)
    events = audit_service.trip_timeline(
        session, context.organization_id, trip_id, limit=limit
    )
    return [
        AuditEventRead(at=event.at, kind=event.kind, action=event.action, actor=event.actor, detail=event.detail)
        for event in events
    ]
