"""主动建议接口：列表、批准、驳回。

这一层只做 HTTP 语义（权限、分页、幂等、组装响应）；规则、抑制与执行全在
:mod:`app.services.proactive_service`。三点与客户端契约（``apps/ios``）对齐的细节：

- 列表响应不是通用 ``Page``，而是 ``{items, daily_cap, next_cursor}``：Today 页要同时展示
  "今天还能收几条提醒"。
- ``approve`` / ``dismiss`` 都没有必填请求体。批准人来自 ``X-User-Id``；驳回原因可选。
- 两个写接口都接受 ``Idempotency-Key``。重放不会写第二条审批记录，也不会重复执行。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Query, status

from app.api.deps import (
    ROLE_TRAVELER,
    ROLE_VIEWER,
    ContextDep,
    IdempotencyKeyDep,
    SessionDep,
    require_role,
)
from app.api.idempotent import IdempotentWrite
from app.api.pagination import clamp_limit, decode_cursor
from app.api.pagination import next_cursor as page_cursor
from app.api.schemas import (
    DailyCapRead,
    MemoryFeedbackRead,
    ProactiveSuggestionRead,
    SuggestionApprovalRead,
    SuggestionApproveRead,
    SuggestionDismissRead,
    SuggestionDismissRequest,
    SuggestionExecutionRead,
    SuggestionFeedRead,
    SuggestionStatus,
)
from app.config import get_settings
from app.services import proactive_service, trip_risk_service, trip_service

router = APIRouter(tags=["proactive"])

APPROVE_ENDPOINT = "POST /v1/proactive/suggestions/{suggestion_id}:approve"
DISMISS_ENDPOINT = "POST /v1/proactive/suggestions/{suggestion_id}:dismiss"

#: 列表默认页大小。Today 页通常只取前 20 条，但允许客户端显式放大（上限由 clamp_limit 管）。
DEFAULT_FEED_LIMIT = 20


@router.get(
    "/proactive/suggestions",
    response_model=SuggestionFeedRead,
    summary="主动建议列表（含今日打扰额度）",
)
def list_suggestions(
    context: ContextDep,
    session: SessionDep,
    trip_id: Annotated[UUID | None, Query(description="只看某次出行")] = None,
    suggestion_status: Annotated[SuggestionStatus | None, Query(alias="status")] = None,
    limit: Annotated[int | None, Query(ge=1)] = None,
    cursor: Annotated[str | None, Query()] = None,
):
    require_role(context, ROLE_VIEWER)
    if trip_id is not None:
        # 先确认出行属于当前租户：否则一个越权 id 会得到空列表而不是 404，客户端无法区分
        # "没有建议"和"这不是你的出行"。
        trip_service.require_trip(session, context.organization_id, trip_id)

    page_size = clamp_limit(limit, default=DEFAULT_FEED_LIMIT, maximum=100)
    offset = decode_cursor(cursor)
    items = proactive_service.list_suggestions(
        session,
        context.organization_id,
        trip_id=trip_id,
        status=suggestion_status,
        limit=page_size,
        offset=offset,
    )
    used = proactive_service.daily_cap_usage(
        session, context.organization_id, context.user_id
    )
    return SuggestionFeedRead(
        items=[ProactiveSuggestionRead.model_validate(item) for item in items],
        daily_cap=DailyCapRead(used=used, cap=get_settings().daily_suggestion_cap),
        next_cursor=page_cursor(items, offset, page_size),
    )


@router.post(
    "/proactive/suggestions/{suggestion_id}:approve",
    response_model=SuggestionApproveRead,
    status_code=status.HTTP_200_OK,
    summary="人审通过并执行（只准备，不对外）",
)
def approve_suggestion(
    suggestion_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=APPROVE_ENDPOINT,
        key=idempotency_key,
        payload={"suggestion_id": str(suggestion_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay

    outcome = proactive_service.approve(
        session,
        context.organization_id,
        suggestion_id,
        actor_user_id=context.user_id,
        actor_role=context.role,
    )
    suggestion = outcome.suggestion
    trip_risk_service.reconcile_trip(session, context.organization_id, suggestion.trip_id)
    execution = suggestion.execution_result or {}
    body = SuggestionApproveRead(
        id=suggestion.id,
        status=suggestion.status,
        approval=SuggestionApprovalRead(
            approved_by=suggestion.approved_by_user_id,
            approved_at=suggestion.approved_at,
        ),
        execution=(
            SuggestionExecutionRead(
                status=execution.get("status"),
                detail=execution.get("detail"),
            )
            if execution
            else None
        ),
        executed=outcome.executed,
        approval_count=outcome.approval_count,
        required_approvals=outcome.required_approvals,
    )
    return guard.finish(status_code=status.HTTP_200_OK, body=body)


@router.post(
    "/proactive/suggestions/{suggestion_id}:dismiss",
    response_model=SuggestionDismissRead,
    status_code=status.HTTP_200_OK,
    summary="驳回建议（负反馈进入误报回收）",
)
def dismiss_suggestion(
    suggestion_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    payload: Annotated[SuggestionDismissRequest | None, Body()] = None,
):
    require_role(context, ROLE_TRAVELER)
    reason = payload.reason if payload is not None else None
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=DISMISS_ENDPOINT,
        key=idempotency_key,
        payload={"suggestion_id": str(suggestion_id), "reason": reason},
    )
    if (replay := guard.begin()) is not None:
        return replay

    outcome = proactive_service.dismiss(
        session,
        context.organization_id,
        suggestion_id,
        actor_user_id=context.user_id,
        reason=reason,
    )
    suggestion = outcome.suggestion
    trip_risk_service.reconcile_trip(session, context.organization_id, suggestion.trip_id)
    body = SuggestionDismissRead(
        id=suggestion.id,
        status=suggestion.status,
        dismissed_reason=suggestion.dismissed_reason,
        memory_feedback=MemoryFeedbackRead(
            type=suggestion.suggestion_type,
            weight_delta=round(outcome.weight_delta, 3),
        ),
    )
    return guard.finish(status_code=status.HTTP_200_OK, body=body)
