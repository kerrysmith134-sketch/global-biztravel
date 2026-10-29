"""通知接口：当前用户的推送记录与已读标记。

设计文档 v2.0 第 8.6 章。通知是"建议已推送"的可见落点；本地默认 ``inapp`` 通道下，
这里返回的就是用户实际能看到的内容。已读只影响未读数，不改变送达语义。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import ROLE_TRAVELER, ROLE_VIEWER, ContextDep, SessionDep, require_role
from app.api.errors import NotFound
from app.api.schemas import (
    NotificationFeedRead,
    NotificationPreferenceRead,
    NotificationPreferenceUpdate,
    NotificationRead,
)
from app.services import notification_service

router = APIRouter(tags=["notifications"])


@router.get("/notifications", response_model=NotificationFeedRead, summary="我的通知")
def list_notifications(
    context: ContextDep,
    session: SessionDep,
    unread_only: Annotated[bool, Query()] = False,
    priority: Annotated[
        str | None, Query(pattern="^P[0-3]$", description="只看某一通知等级")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    require_role(context, ROLE_VIEWER)
    rows = notification_service.list_for_user(
        session,
        context.organization_id,
        context.user_id,
        unread_only=unread_only,
        priority=priority,
        limit=limit,
    )
    unread = notification_service.unread_count(
        session, context.organization_id, context.user_id
    )
    return NotificationFeedRead(
        items=[NotificationRead.model_validate(row) for row in rows],
        unread=unread,
        unread_by_priority=notification_service.unread_by_priority(
            session, context.organization_id, context.user_id
        ),
    )


@router.post(
    "/notifications/{notification_id}:read",
    response_model=NotificationRead,
    status_code=status.HTTP_200_OK,
    summary="标记通知已读（幂等）",
)
def read_notification(
    notification_id: UUID, context: ContextDep, session: SessionDep
):
    require_role(context, ROLE_VIEWER)
    row = notification_service.mark_read(
        session, context.organization_id, context.user_id, notification_id
    )
    if row is None:
        raise NotFound("通知不存在", details={"notification_id": str(notification_id)})
    return NotificationRead.model_validate(row)


@router.get(
    "/notifications/preferences",
    response_model=NotificationPreferenceRead,
    summary="我的通知偏好（静音的等级不产生通知）",
)
def get_preferences(context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    row = notification_service.get_preference(
        session, context.organization_id, context.user_id
    )
    return NotificationPreferenceRead(muted_priorities=list(row.muted_priorities or []))


@router.put(
    "/notifications/preferences",
    response_model=NotificationPreferenceRead,
    summary="更新通知偏好（幂等覆盖静音等级列表）",
)
def update_preferences(
    payload: NotificationPreferenceUpdate, context: ContextDep, session: SessionDep
):
    require_role(context, ROLE_TRAVELER)
    row = notification_service.update_preference(
        session,
        context.organization_id,
        context.user_id,
        list(payload.muted_priorities),
    )
    return NotificationPreferenceRead(muted_priorities=list(row.muted_priorities or []))
