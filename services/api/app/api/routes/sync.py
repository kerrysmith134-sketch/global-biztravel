"""离线批次同步接口：``POST /v1/sync:push``。

返回逐项结果而不是单一成功/失败（设计文档第 10.5 章）。整批请求带 ``Idempotency-Key``：
弱网下客户端重发同一批时，不会把已经 applied 的拜访记录再写一遍；未带键时，记录级
``client_record_id`` 仍然保证同一条笔记不会重复落库。
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import (
    ROLE_TRAVELER,
    ContextDep,
    IdempotencyKeyDep,
    SessionDep,
    require_role,
)
from app.api.idempotent import IdempotentWrite
from app.api.schemas import (
    ConflictRecordRead,
    SyncItemResultRead,
    SyncPushRead,
    SyncPushRequest,
    SyncSummaryRead,
)
from app.services import sync_service

router = APIRouter(tags=["sync"])

SYNC_PUSH_ENDPOINT = "POST /v1/sync:push"


@router.post(
    "/sync:push",
    response_model=SyncPushRead,
    status_code=status.HTTP_200_OK,
    summary="离线批次同步（逐项结果，部分成功可接受）",
)
def push_sync_batch(
    payload: SyncPushRequest,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=SYNC_PUSH_ENDPOINT,
        key=idempotency_key,
        payload=payload.model_dump(mode="json"),
    )
    if (replay := guard.begin()) is not None:
        return replay

    outcome = sync_service.push(
        session,
        context.organization_id,
        payload,
        actor_user_id=context.user_id,
    )
    body = SyncPushRead(
        results=[
            SyncItemResultRead(
                item_id=item.item_id,
                entity=item.entity,
                status=item.status,
                server_version=item.server_version,
                conflict=(
                    ConflictRecordRead.model_validate(item.conflict)
                    if item.conflict is not None
                    else None
                ),
            )
            for item in outcome.results
        ],
        summary=SyncSummaryRead(**outcome.summary),
    )
    return guard.finish(status_code=status.HTTP_200_OK, body=body)
