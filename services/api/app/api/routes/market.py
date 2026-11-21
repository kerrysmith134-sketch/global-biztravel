"""市场 Workspace API。路由仅处理 HTTP 语义、权限与幂等；规则位于 market_workspace_service。"""

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
from app.api.errors import NotFound
from app.api.idempotent import IdempotentWrite
from app.api.pagination import clamp_limit, decode_cursor
from app.api.pagination import next_cursor as page_cursor
from app.api.schemas import (
    MarketActivityEventRead,
    MarketContextCreate,
    MarketContextRead,
    MarketResearchQuestionCreate,
    MarketResearchQuestionRead,
    MarketWorkspaceCreate,
    MarketWorkspaceRead,
    ResearchPlanRead,
    Page,
)
from app.services import market_workspace_service, research_service

router = APIRouter(tags=["market intelligence"])

WORKSPACES_ENDPOINT = "POST /v1/market/workspaces"


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
    rows = market_workspace_service.list_workspaces(
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
    summary="创建市场工作空间",
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
        endpoint=WORKSPACES_ENDPOINT,
        key=idempotency_key,
        payload=payload.model_dump(mode="json"),
    )
    if (replay := guard.begin()) is not None:
        return replay
    workspace = market_workspace_service.create_workspace(
        session,
        context.organization_id,
        name=payload.name,
        objective=payload.objective,
        settings=payload.settings,
        owner_user_id=context.user_id,
        created_by_user_id=context.user_id,
        initial_context=payload.initial_context,
    )
    return guard.finish(
        status_code=status.HTTP_201_CREATED,
        body=MarketWorkspaceRead.model_validate(workspace),
    )


@router.get(
    "/market/workspaces/{workspace_id}",
    response_model=MarketWorkspaceRead,
    summary="市场工作空间详情",
)
def get_workspace(workspace_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    workspace = market_workspace_service.get_workspace(
        session, context.organization_id, workspace_id
    )
    if workspace is None:
        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})
    return MarketWorkspaceRead.model_validate(workspace)


@router.get(
    "/market/workspaces/{workspace_id}/contexts",
    response_model=list[MarketContextRead],
    summary="工作空间上下文版本列表",
)
def list_contexts(workspace_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    if market_workspace_service.get_workspace(session, context.organization_id, workspace_id) is None:
        raise NotFound("市场工作空间不存在", details={"workspace_id": str(workspace_id)})
    rows = market_workspace_service.list_contexts(
        session, context.organization_id, workspace_id
    )
    return [MarketContextRead.model_validate(row) for row in rows]


@router.post(
    "/market/workspaces/{workspace_id}/contexts",
    response_model=MarketContextRead,
    status_code=status.HTTP_201_CREATED,
    summary="新增业务上下文版本",
)
def create_context(
    workspace_id: UUID,
    payload: MarketContextCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = f"POST /v1/market/workspaces/{workspace_id}/contexts"
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    snapshot = market_workspace_service.create_context_snapshot(
        session,
        context.organization_id,
        workspace_id,
        raw_input=payload.raw_input,
        adopt=payload.adopt,
        created_by_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_201_CREATED,
        body=MarketContextRead.model_validate(snapshot),
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
    endpoint = f"POST /v1/market/workspaces/{workspace_id}/contexts/{context_id}:adopt"
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), "context_id": str(context_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay
    snapshot = market_workspace_service.adopt_context_snapshot(
        session,
        context.organization_id,
        workspace_id,
        context_id,
        actor_user_id=context.user_id,
    )
    return guard.finish(status_code=status.HTTP_200_OK, body=MarketContextRead.model_validate(snapshot))


@router.get(
    "/market/workspaces/{workspace_id}/questions",
    response_model=Page[MarketResearchQuestionRead],
    summary="市场研究问题列表",
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
    rows = market_workspace_service.list_research_questions(
        session, context.organization_id, workspace_id, limit=page_size, offset=offset
    )
    return Page[MarketResearchQuestionRead](
        items=[MarketResearchQuestionRead.model_validate(row) for row in rows],
        next_cursor=page_cursor(rows, offset, page_size),
    )


@router.post(
    "/market/workspaces/{workspace_id}/questions",
    response_model=MarketResearchQuestionRead,
    status_code=status.HTTP_201_CREATED,
    summary="新增市场研究问题",
)
def create_question(
    workspace_id: UUID,
    payload: MarketResearchQuestionCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = f"POST /v1/market/workspaces/{workspace_id}/questions"
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    item = market_workspace_service.create_research_question(
        session,
        context.organization_id,
        workspace_id,
        question=payload.question,
        rationale=payload.rationale,
        priority=payload.priority,
        acceptance_criteria=payload.acceptance_criteria,
        parent_question_id=payload.parent_question_id,
        created_by_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_201_CREATED,
        body=MarketResearchQuestionRead.model_validate(item),
    )


@router.get(
    "/market/workspaces/{workspace_id}/research-plans",
    response_model=list[ResearchPlanRead],
    summary="读取工作空间关联的研究计划",
)
def list_workspace_research_plans(
    workspace_id: UUID,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_VIEWER)
    return research_service.list_research_plans_for_workspace(
        session, context.organization_id, workspace_id
    )


@router.get(
    "/market/workspaces/{workspace_id}/activity",
    response_model=list[MarketActivityEventRead],
    summary="工作空间活动时间线",
)
def list_activity(
    workspace_id: UUID,
    context: ContextDep,
    session: SessionDep,
    limit: int = Query(default=100, ge=1, le=200),
):
    require_role(context, ROLE_VIEWER)
    rows = market_workspace_service.list_activity_events(
        session, context.organization_id, workspace_id, limit=limit
    )
    return [MarketActivityEventRead.model_validate(row) for row in rows]


__all__ = ["router"]
