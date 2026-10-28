"""行动中心（PRD S8-08 / 页面 06）。"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import ROLE_VIEWER, ContextDep, SessionDep, require_role
from app.api.schemas import ActionCenterRead
from app.services import action_center_service

router = APIRouter(tags=["action-center"])


@router.get(
    "/action-center",
    response_model=ActionCenterRead,
    summary="聚合待决策、待审批、执行中、失败与待外部确认事项",
)
def get_action_center(
    context: ContextDep,
    session: SessionDep,
    trip_id: Annotated[
        UUID | None, Query(description="可选：只看某次出行")
    ] = None,
):
    require_role(context, ROLE_VIEWER)
    return action_center_service.build_action_center(
        session, context.organization_id, trip_id=trip_id
    )
