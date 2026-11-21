"""市场 Workspace API。路由仅处理 HTTP 语义、权限与幂等；规则位于 market_workspace_service。"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import (
    ROLE_ADMIN,
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
    MarketEntityCustomerLinkCreate,
    MarketEntityCustomerLinkRead,
    MarketEvidenceCreate,
    MarketEvidenceRead,
    MarketInsightCreate,
    MarketInsightRead,
    MarketInsightReview,
    MarketResearchQuestionCreate,
    MarketResearchQuestionRead,
    MarketWorkspaceCreate,
    MarketWorkspaceEntityCreate,
    MarketWorkspaceEntityRead,
    MarketWorkspaceRead,
    Page,
    ResearchPlanRead,
)
from app.services import (
    market_entity_service,
    market_insight_service,
    market_workspace_service,
    research_service,
)

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
    "/market/workspaces/{workspace_id}/entities",
    response_model=Page[MarketWorkspaceEntityRead],
    summary="工作空间候选实体列表",
)
def list_workspace_entities(
    workspace_id: UUID,
    context: ContextDep,
    session: SessionDep,
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None),
):
    require_role(context, ROLE_VIEWER)
    page_size = clamp_limit(limit)
    offset = decode_cursor(cursor)
    rows = market_entity_service.list_workspace_entities(
        session,
        context.organization_id,
        workspace_id,
        limit=page_size,
        offset=offset,
    )
    return Page[MarketWorkspaceEntityRead](
        items=[MarketWorkspaceEntityRead.model_validate(row) for row in rows],
        next_cursor=page_cursor(rows, offset, page_size),
    )


@router.post(
    "/market/workspaces/{workspace_id}/entities",
    response_model=MarketWorkspaceEntityRead,
    status_code=status.HTTP_201_CREATED,
    summary="向工作空间添加候选实体",
)
def create_workspace_entity(
    workspace_id: UUID,
    payload: MarketWorkspaceEntityCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = f"POST /v1/market/workspaces/{workspace_id}/entities"
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    item = market_entity_service.create_workspace_entity(
        session,
        context.organization_id,
        workspace_id,
        existing_entity_id=payload.existing_entity_id,
        entity_kind=payload.entity_kind,
        display_name=payload.display_name,
        description=payload.description,
        canonical_domain=payload.canonical_domain,
        country_code=payload.country_code,
        attributes=payload.attributes,
        discovery_reason=payload.discovery_reason,
        relevance_note=payload.relevance_note,
        created_by_user_id=context.user_id,
    )
    return guard.finish(status_code=status.HTTP_201_CREATED, body=item)


@router.post(
    "/market/entities/{entity_id}/customer-links",
    response_model=MarketEntityCustomerLinkRead,
    status_code=status.HTTP_201_CREATED,
    summary="创建候选实体与正式客户的匹配建议",
)
def create_customer_link_candidate(
    entity_id: UUID,
    payload: MarketEntityCustomerLinkCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = f"POST /v1/market/entities/{entity_id}/customer-links"
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"entity_id": str(entity_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    link = market_entity_service.create_customer_link_candidate(
        session,
        context.organization_id,
        entity_id,
        customer_id=payload.customer_id,
        matching_basis=payload.matching_basis,
        actor_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_201_CREATED,
        body=MarketEntityCustomerLinkRead.model_validate(link),
    )


@router.post(
    "/market/entities/{entity_id}/customer-links/{link_id}:confirm",
    response_model=MarketEntityCustomerLinkRead,
    summary="明确确认候选实体与正式客户的关联",
)
def confirm_customer_link(
    entity_id: UUID,
    link_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_ADMIN)
    endpoint = f"POST /v1/market/entities/{entity_id}/customer-links/{link_id}:confirm"
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"entity_id": str(entity_id), "link_id": str(link_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay
    link = market_entity_service.confirm_customer_link(
        session,
        context.organization_id,
        entity_id,
        link_id,
        actor_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_200_OK,
        body=MarketEntityCustomerLinkRead.model_validate(link),
    )


@router.post(
    "/market/workspaces/{workspace_id}/evidence",
    response_model=MarketEvidenceRead,
    status_code=status.HTTP_201_CREATED,
    summary="保存工作空间来源证据的内容版本",
)
def create_market_evidence(
    workspace_id: UUID,
    payload: MarketEvidenceCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = f"POST /v1/market/workspaces/{workspace_id}/evidence"
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_insight_service.record_market_evidence(
        session,
        context.organization_id,
        workspace_id,
        canonical_ref=payload.canonical_ref,
        content=payload.content,
        kind=payload.kind,
        source=payload.source,
        published_at=payload.published_at,
        observation=payload.observation,
        verification_status=payload.verification_status,
        quality=payload.quality,
        payload=payload.payload,
    )
    return guard.finish(
        status_code=status.HTTP_201_CREATED,
        body=MarketEvidenceRead.model_validate(row),
    )


@router.get(
    "/market/workspaces/{workspace_id}/evidence",
    response_model=Page[MarketEvidenceRead],
    summary="工作空间来源证据列表",
)
def list_market_evidence(
    workspace_id: UUID,
    context: ContextDep,
    session: SessionDep,
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None),
):
    require_role(context, ROLE_VIEWER)
    page_size = clamp_limit(limit)
    offset = decode_cursor(cursor)
    rows = market_insight_service.list_workspace_evidence(
        session, context.organization_id, workspace_id, limit=page_size, offset=offset
    )
    return Page[MarketEvidenceRead](
        items=[MarketEvidenceRead.model_validate(row) for row in rows],
        next_cursor=page_cursor(rows, offset, page_size),
    )


@router.post(
    "/market/workspaces/{workspace_id}/insights",
    response_model=MarketInsightRead,
    status_code=status.HTTP_201_CREATED,
    summary="创建带证据链的市场判断草稿",
)
def create_market_insight(
    workspace_id: UUID,
    payload: MarketInsightCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = f"POST /v1/market/workspaces/{workspace_id}/insights"
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_insight_service.create_insight(
        session,
        context.organization_id,
        workspace_id,
        insight_key=payload.insight_key,
        insight_type=payload.insight_type,
        statement=payload.statement,
        assumptions=payload.assumptions,
        unknowns=payload.unknowns,
        implications=payload.implications,
        confidence_level=payload.confidence_level,
        generated_by_run_id=payload.generated_by_run_id,
        evidence=[item.model_dump() for item in payload.evidence],
        insufficient_evidence_reason=payload.insufficient_evidence_reason,
        created_by_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_201_CREATED,
        body=MarketInsightRead.model_validate(row),
    )


@router.get(
    "/market/workspaces/{workspace_id}/insights",
    response_model=Page[MarketInsightRead],
    summary="工作空间市场判断列表",
)
def list_market_insights(
    workspace_id: UUID,
    context: ContextDep,
    session: SessionDep,
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None),
):
    require_role(context, ROLE_VIEWER)
    page_size = clamp_limit(limit)
    offset = decode_cursor(cursor)
    rows = market_insight_service.list_insights(
        session, context.organization_id, workspace_id, limit=page_size, offset=offset
    )
    return Page[MarketInsightRead](
        items=[MarketInsightRead.model_validate(row) for row in rows],
        next_cursor=page_cursor(rows, offset, page_size),
    )


def _review_market_insight(
    decision: str,
    workspace_id: UUID,
    insight_id: UUID,
    payload: MarketInsightReview,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    action = "accept" if decision == "ACCEPTED" else "reject"
    endpoint = f"POST /v1/market/workspaces/{workspace_id}/insights/{insight_id}:{action}"
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={
            "workspace_id": str(workspace_id),
            "insight_id": str(insight_id),
            "review_note": payload.review_note,
        },
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_insight_service.review_insight(
        session,
        context.organization_id,
        workspace_id,
        insight_id,
        decision=decision,
        actor_user_id=context.user_id,
        review_note=payload.review_note,
    )
    return guard.finish(
        status_code=status.HTTP_200_OK,
        body=MarketInsightRead.model_validate(row),
    )


@router.post(
    "/market/workspaces/{workspace_id}/insights/{insight_id}:accept",
    response_model=MarketInsightRead,
    summary="人工采纳市场判断",
)
def accept_market_insight(
    workspace_id: UUID,
    insight_id: UUID,
    payload: MarketInsightReview,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    return _review_market_insight(
        "ACCEPTED", workspace_id, insight_id, payload, context, session, idempotency_key
    )


@router.post(
    "/market/workspaces/{workspace_id}/insights/{insight_id}:reject",
    response_model=MarketInsightRead,
    summary="人工驳回市场判断",
)
def reject_market_insight(
    workspace_id: UUID,
    insight_id: UUID,
    payload: MarketInsightReview,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    return _review_market_insight(
        "REJECTED", workspace_id, insight_id, payload, context, session, idempotency_key
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
