"""Market Intelligence 工作空间基础 API。"""

from __future__ import annotations

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
from app.api.pagination import clamp_limit, decode_cursor
from app.api.pagination import next_cursor as page_cursor
from app.api.schemas import Page
from app.api.market_schemas import (
    MarketActivityEventRead,
    MarketContextCreate,
    MarketContextRead,
    MarketResearchQuestionCreate,
    MarketResearchQuestionRead,
    MarketResearchQuestionUpdate,
    MarketWorkspaceCreate,
    MarketWorkspaceRead,
    MarketWorkspaceUpdate,
)
from app.services import market_service

router = APIRouter(tags=["market"])


@router.get(
    "/market/workspaces",
    response_model=Page[MarketWorkspaceRead],
    summary="市场工作空间列表",
)
def list_workspaces(
    context: ContextDep,
    session: SessionDep,
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None),
):
    require_role(context, ROLE_VIEWER)
    page_size = clamp_limit(limit)
    offset = decode_cursor(cursor)
    rows = market_service.list_workspaces(
        session, context.organization_id, limit=page_size, offset=offset
    )
    return Page[MarketWorkspaceRead](
        items=[MarketWorkspaceRead.model_validate(row) for row in rows],
        next_cursor=page_cursor(rows, offset, page_size),
    )


@router.post(
    "/market/workspaces",
    response_model=MarketWorkspaceRead,
    status_code=status.HTTP_201_CREATED,
    summary="创建市场工作空间并保留原始业务输入",
)
def create_workspace(
    payload: MarketWorkspaceCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint="POST /v1/market/workspaces",
        key=idempotency_key,
        payload=payload.model_dump(mode="json"),
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_service.create_workspace(
        session,
        context.organization_id,
        owner_user_id=context.user_id,
        name=payload.name,
        objective=payload.objective,
        raw_input=payload.raw_input,
        settings=payload.settings,
    )
    return guard.finish(
        status_code=status.HTTP_201_CREATED,
        body=MarketWorkspaceRead.model_validate(row),
    )


@router.get(
    "/market/workspaces/{workspace_id}",
    response_model=MarketWorkspaceRead,
    summary="市场工作空间详情",
)
def get_workspace(
    workspace_id: UUID,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_VIEWER)
    row = market_service.get_workspace(session, context.organization_id, workspace_id)
    if row is None:
        from app.api.errors import NotFound

        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})
    return MarketWorkspaceRead.model_validate(row)


@router.patch(
    "/market/workspaces/{workspace_id}",
    response_model=MarketWorkspaceRead,
    summary="修改市场工作空间基本信息",
)
def update_workspace(
    workspace_id: UUID,
    payload: MarketWorkspaceUpdate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    changes = payload.model_dump(exclude_unset=True)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=f"PATCH /v1/market/workspaces/{workspace_id}",
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), "changes": changes},
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_service.update_workspace(
        session,
        context.organization_id,
        workspace_id,
        actor_user_id=context.user_id,
        changes=changes,
    )
    return guard.finish(
        status_code=status.HTTP_200_OK,
        body=MarketWorkspaceRead.model_validate(row),
    )


@router.get(
    "/market/workspaces/{workspace_id}/contexts",
    response_model=list[MarketContextRead],
    summary="读取工作空间的上下文版本",
)
def list_contexts(
    workspace_id: UUID,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_VIEWER)
    rows = market_service.list_contexts(session, context.organization_id, workspace_id)
    return [MarketContextRead.model_validate(row) for row in rows]


@router.post(
    "/market/workspaces/{workspace_id}/contexts",
    response_model=MarketContextRead,
    status_code=status.HTTP_201_CREATED,
    summary="创建新的业务上下文版本（不覆盖历史版本）",
)
def create_context(
    workspace_id: UUID,
    payload: MarketContextCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=f"POST /v1/market/workspaces/{workspace_id}/contexts",
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_service.create_context_snapshot(
        session,
        context.organization_id,
        workspace_id,
        actor_user_id=context.user_id,
        raw_input=payload.raw_input,
        user_overrides=payload.user_overrides,
    )
    return guard.finish(
        status_code=status.HTTP_201_CREATED,
        body=MarketContextRead.model_validate(row),
    )


@router.post(
    "/market/workspaces/{workspace_id}/contexts/{context_id}:adopt",
    response_model=MarketContextRead,
    summary="采纳一个业务上下文版本",
)
def adopt_context(
    workspace_id: UUID,
    context_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=f"POST /v1/market/workspaces/{workspace_id}/contexts/{context_id}:adopt",
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), "context_id": str(context_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_service.adopt_context_snapshot(
        session,
        context.organization_id,
        workspace_id,
        context_id,
        actor_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_200_OK,
        body=MarketContextRead.model_validate(row),
    )


@router.get(
    "/market/workspaces/{workspace_id}/questions",
    response_model=Page[MarketResearchQuestionRead],
    summary="研究问题列表",
)
def list_questions(
    workspace_id: UUID,
    context: ContextDep,
    session: SessionDep,
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None),
):
    require_role(context, ROLE_VIEWER)
    page_size = clamp_limit(limit)
    offset = decode_cursor(cursor)
    rows = market_service.list_questions(
        session,
        context.organization_id,
        workspace_id,
        limit=page_size,
        offset=offset,
    )
    return Page[MarketResearchQuestionRead](
        items=[MarketResearchQuestionRead.model_validate(row) for row in rows],
        next_cursor=page_cursor(rows, offset, page_size),
    )


@router.post(
    "/market/workspaces/{workspace_id}/questions",
    response_model=MarketResearchQuestionRead,
    status_code=status.HTTP_201_CREATED,
    summary="新增研究问题",
)
def create_question(
    workspace_id: UUID,
    payload: MarketResearchQuestionCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=f"POST /v1/market/workspaces/{workspace_id}/questions",
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_service.create_question(
        session,
        context.organization_id,
        workspace_id,
        actor_user_id=context.user_id,
        question=payload.question,
        rationale=payload.rationale,
        priority=payload.priority,
        parent_question_id=payload.parent_question_id,
        acceptance_criteria=payload.acceptance_criteria,
    )
    return guard.finish(
        status_code=status.HTTP_201_CREATED,
        body=MarketResearchQuestionRead.model_validate(row),
    )


@router.patch(
    "/market/questions/{question_id}",
    response_model=MarketResearchQuestionRead,
    summary="修改研究问题内容，不直接修改工作流状态",
)
def update_question(
    question_id: UUID,
    payload: MarketResearchQuestionUpdate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    changes = payload.model_dump(exclude_unset=True)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=f"PATCH /v1/market/questions/{question_id}",
        key=idempotency_key,
        payload={"question_id": str(question_id), "changes": changes},
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_service.update_question(
        session,
        context.organization_id,
        question_id,
        actor_user_id=context.user_id,
        changes=changes,
    )
    return guard.finish(
        status_code=status.HTTP_200_OK,
        body=MarketResearchQuestionRead.model_validate(row),
    )


@router.get(
    "/market/workspaces/{workspace_id}/activity",
    response_model=Page[MarketActivityEventRead],
    summary="工作空间活动审计时间线",
)
def list_activity(
    workspace_id: UUID,
    context: ContextDep,
    session: SessionDep,
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None),
):
    require_role(context, ROLE_VIEWER)
    page_size = clamp_limit(limit)
    offset = decode_cursor(cursor)
    rows = market_service.list_activity(
        session,
        context.organization_id,
        workspace_id,
        limit=page_size,
        offset=offset,
    )
    return Page[MarketActivityEventRead](
        items=[MarketActivityEventRead.model_validate(row) for row in rows],
        next_cursor=page_cursor(rows, offset, page_size),
    )
