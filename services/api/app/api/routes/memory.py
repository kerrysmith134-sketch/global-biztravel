"""Agent 记忆接口：行程记忆、混合检索、出差人画像。

设计文档 v2.0 第 9 章。路由只做权限与组装；版本化、检索融合与复盘生成都在
:mod:`app.services.memory_service`。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_TRAVELER,
    ROLE_VIEWER,
    ContextDep,
    SessionDep,
    require_role,
)
from app.api.schemas import (
    MemoryForgetRead,
    MemoryProcedureRead,
    MemorySearchItemRead,
    MemorySearchRead,
    MemorySummaryRead,
    TravelerProfileRead,
    TravelerProfileUpdate,
    TripMemoryRead,
)
from app.services import memory_service, trip_service

router = APIRouter(tags=["memory"])


def _summary_read(item) -> MemorySummaryRead:  # noqa: ANN001 —— ORM 对象，避免在路由层导入模型类型
    return MemorySummaryRead(
        id=item.id,
        kind=item.kind,
        summary=item.summary,
        decisions=list(item.decisions or []),
        follow_ups=list(item.follow_ups or []),
        lessons=list(item.lessons or []),
        valid_from=item.valid_from,
        confidence=item.confidence,
    )


def _trip_memory_read(session, organization_id, trip_id) -> TripMemoryRead:  # noqa: ANN001
    summaries, procedure = memory_service.get_trip_memory(session, organization_id, trip_id)
    return TripMemoryRead(
        trip_id=trip_id,
        summaries=[_summary_read(item) for item in summaries],
        procedure=[
            MemoryProcedureRead(
                attempt_id=attempt.id,
                outcome=attempt.attempt_type,
                reason=attempt.reason,
                created_at=attempt.created_at,
            )
            for attempt in procedure
        ],
    )


@router.get(
    "/trips/{trip_id}/memory",
    response_model=TripMemoryRead,
    summary="行程记忆（摘要 + 过程记录）",
)
def get_trip_memory(trip_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    trip_service.require_trip(session, context.organization_id, trip_id)
    return _trip_memory_read(session, context.organization_id, trip_id)


@router.post(
    "/trips/{trip_id}/memory:review",
    response_model=TripMemoryRead,
    summary="手动触发复盘（正常由 Worker 在行程完成后执行）",
)
def review_trip_memory(trip_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_ADMIN)
    trip_service.require_trip(session, context.organization_id, trip_id)
    memory_service.review_trip(session, context.organization_id, trip_id)
    return _trip_memory_read(session, context.organization_id, trip_id)


@router.get("/memory:search", response_model=MemorySearchRead, summary="记忆混合检索（关键词 + 向量）")
def search_memory(
    context: ContextDep,
    session: SessionDep,
    q: Annotated[str, Query(min_length=1, max_length=200, description="检索词")],
    trip_id: Annotated[UUID | None, Query(description="限定某次出行")] = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
):
    require_role(context, ROLE_VIEWER)
    if trip_id is not None:
        trip_service.require_trip(session, context.organization_id, trip_id)
    hits = memory_service.search_memory(
        session, context.organization_id, q, trip_id=trip_id, limit=limit
    )
    return MemorySearchRead(
        query=q,
        items=[
            MemorySearchItemRead(**_summary_read(hit.summary).model_dump(), score=round(hit.score, 6))
            for hit in hits
        ],
    )


@router.get("/traveler/profile", response_model=TravelerProfileRead, summary="当前出差人画像")
def get_profile(context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    profile = memory_service.get_profile(session, context.organization_id, context.user_id)
    if profile is None:
        return TravelerProfileRead(version=0, preferences={}, notes=None, confidence=0.0)
    return TravelerProfileRead(
        version=profile.version,
        preferences=dict(profile.preferences or {}),
        notes=profile.notes,
        confidence=profile.confidence,
    )


@router.put("/traveler/profile", response_model=TravelerProfileRead, summary="更新当前出差人画像")
def update_profile(
    payload: TravelerProfileUpdate, context: ContextDep, session: SessionDep
):
    require_role(context, ROLE_TRAVELER)
    profile = memory_service.update_profile(
        session,
        context.organization_id,
        context.user_id,
        preferences=payload.preferences,
        notes=payload.notes,
        confidence=payload.confidence,
    )
    return TravelerProfileRead(
        version=profile.version,
        preferences=dict(profile.preferences or {}),
        notes=profile.notes,
        confidence=profile.confidence,
    )


@router.delete(
    "/memory/{summary_id}",
    response_model=MemoryForgetRead,
    summary="合规删除一条记忆（清空正文与向量）",
)
def forget_memory(summary_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_ADMIN)
    item = memory_service.forget_summary(session, context.organization_id, summary_id)
    return MemoryForgetRead(id=item.id, forgotten=True, valid_until=item.valid_until)
