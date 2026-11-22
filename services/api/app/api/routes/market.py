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
    MarketClaimRead,
    MarketClaimReview,
    MarketClaimVerificationAccepted,
    MarketClaimVerificationRunRead,
    MarketContextCreate,
    MarketContextRead,
    MarketEntityCustomerLinkCreate,
    MarketEntityCustomerLinkRead,
    MarketEntityRead,
    MarketEvidenceCreate,
    MarketEvidenceIntelligenceRead,
    MarketEvidenceRead,
    MarketInsightCreate,
    MarketInsightRead,
    MarketInsightReview,
    MarketOpportunityAssessmentCreate,
    MarketOpportunityAssessmentRead,
    MarketOpportunityCreate,
    MarketOpportunityDetailRead,
    MarketOpportunityEntitiesAttach,
    MarketOpportunityEvidenceCreate,
    MarketOpportunityEvidenceRead,
    MarketOpportunityOutcome,
    MarketOpportunityQualify,
    MarketOpportunityRead,
    MarketOpportunityStatus,
    MarketOpportunityTransition,
    MarketQuestionAnswerCreate,
    MarketQuestionAnswerGenerationAccepted,
    MarketQuestionAnswerGenerationCreate,
    MarketQuestionAnswerRead,
    MarketQuestionAnswerReview,
    MarketQuestionDependencyCreate,
    MarketQuestionDependencyDeleted,
    MarketQuestionDependencyRead,
    MarketResearchGraphExecutionAccepted,
    MarketResearchGraphExecutionCancelled,
    MarketResearchGraphExecutionCreate,
    MarketResearchGraphExecutionRead,
    MarketResearchGraphQuestionRetryAccepted,
    MarketResearchGraphRead,
    MarketResearchQuestionCreate,
    MarketResearchQuestionRead,
    MarketSourceExtractionAccepted,
    MarketSourceExtractionRead,
    MarketSourceVerificationPlanRead,
    MarketWorkspaceCreate,
    MarketWorkspaceEntityCreate,
    MarketWorkspaceEntityRead,
    MarketWorkspaceRead,
    MarketWorkspaceTripLinkCreate,
    MarketWorkspaceTripLinkRead,
    Page,
    ResearchPlanRead,
)
from app.services import (
    market_claim_verification_service,
    market_context_extraction_service,
    market_entity_service,
    market_evidence_intelligence_service,
    market_insight_service,
    market_opportunity_service,
    market_question_answer_agent_service,
    market_question_answer_service,
    market_research_dag_service,
    market_research_graph_service,
    market_source_extraction_service,
    market_source_verification_service,
    market_workspace_service,
    research_service,
)

router = APIRouter(tags=["market intelligence"])

WORKSPACES_ENDPOINT = "POST /v1/market/workspaces"
OPPORTUNITY_STATUS_QUERY = Query(default=None, alias="status")
ANSWER_ID_QUERY = Query(default=None)
TARGET_QUESTION_ID_QUERY = Query(default=None)


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


@router.post(
    "/market/workspaces/{workspace_id}/contexts/{context_id}:extract",
    response_model=MarketContextRead,
    summary="从用户原始业务背景提取结构化上下文",
)
def extract_context(
    workspace_id: UUID,
    context_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = f"POST /v1/market/workspaces/{workspace_id}/contexts/{context_id}:extract"
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), "context_id": str(context_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay
    snapshot = market_context_extraction_service.extract_context_snapshot(
        session,
        context.organization_id,
        workspace_id,
        context_id,
        actor_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_200_OK,
        body=MarketContextRead.model_validate(snapshot),
    )


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
    "/market/workspaces/{workspace_id}/research-graph",
    response_model=MarketResearchGraphRead,
    summary="读取研究问题依赖图与可执行状态",
)
def get_market_research_graph(
    workspace_id: UUID,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_VIEWER)
    result = market_research_graph_service.get_research_graph(
        session, context.organization_id, workspace_id
    )
    return MarketResearchGraphRead.model_validate(result)


@router.post(
    "/market/workspaces/{workspace_id}/research-graph/executions",
    response_model=MarketResearchGraphExecutionAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="启动持久化研究图执行",
)
def start_market_research_graph_execution(
    workspace_id: UUID,
    payload: MarketResearchGraphExecutionCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = f"POST /v1/market/workspaces/{workspace_id}/research-graph/executions"
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    result = market_research_dag_service.start_execution(
        session,
        context.organization_id,
        workspace_id,
        user_id=context.user_id,
        max_concurrency=payload.max_concurrency,
        max_questions=payload.max_questions,
        max_duration_minutes=payload.max_duration_minutes,
        max_cost=payload.max_cost,
    )
    return guard.finish(
        status_code=status.HTTP_202_ACCEPTED,
        body=MarketResearchGraphExecutionAccepted.model_validate(result),
    )


@router.get(
    "/market/workspaces/{workspace_id}/research-graph/executions/{execution_id}",
    response_model=MarketResearchGraphExecutionRead,
    summary="读取研究图执行进度",
)
def get_market_research_graph_execution(
    workspace_id: UUID,
    execution_id: UUID,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_VIEWER)
    result = market_research_dag_service.get_execution(
        session, context.organization_id, workspace_id, execution_id
    )
    return MarketResearchGraphExecutionRead.model_validate(result)


@router.post(
    "/market/workspaces/{workspace_id}/research-graph/executions/{execution_id}:cancel",
    response_model=MarketResearchGraphExecutionCancelled,
    summary="取消研究图执行",
)
def cancel_market_research_graph_execution(
    workspace_id: UUID,
    execution_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = (
        f"POST /v1/market/workspaces/{workspace_id}/research-graph/"
        f"executions/{execution_id}:cancel"
    )
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), "execution_id": str(execution_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay
    result = market_research_dag_service.cancel_execution(
        session,
        context.organization_id,
        workspace_id,
        execution_id,
        actor_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_200_OK,
        body=MarketResearchGraphExecutionCancelled.model_validate(result),
    )


@router.post(
    "/market/workspaces/{workspace_id}/research-graph/executions/{execution_id}/questions/{question_id}:retry",
    response_model=MarketResearchGraphQuestionRetryAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="显式重试一个失败或被驳回的研究问题",
)
def retry_market_research_graph_question(
    workspace_id: UUID,
    execution_id: UUID,
    question_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = (
        f"POST /v1/market/workspaces/{workspace_id}/research-graph/"
        f"executions/{execution_id}/questions/{question_id}:retry"
    )
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={
            "workspace_id": str(workspace_id),
            "execution_id": str(execution_id),
            "question_id": str(question_id),
        },
    )
    if (replay := guard.begin()) is not None:
        return replay
    result = market_research_dag_service.retry_question(
        session,
        context.organization_id,
        workspace_id,
        execution_id,
        question_id,
        actor_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_202_ACCEPTED,
        body=MarketResearchGraphQuestionRetryAccepted.model_validate(result),
    )


@router.get(
    "/market/workspaces/{workspace_id}/questions/{question_id}/dependencies",
    response_model=list[MarketQuestionDependencyRead],
    summary="读取研究问题的前置依赖",
)
def list_market_question_dependencies(
    workspace_id: UUID,
    question_id: UUID,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_VIEWER)
    rows = market_research_graph_service.list_question_dependencies(
        session, context.organization_id, workspace_id, question_id
    )
    return [MarketQuestionDependencyRead.model_validate(row) for row in rows]


@router.post(
    "/market/workspaces/{workspace_id}/questions/{question_id}/dependencies",
    response_model=MarketQuestionDependencyRead,
    status_code=status.HTTP_201_CREATED,
    summary="新增研究问题依赖（服务端阻止循环依赖）",
)
def create_market_question_dependency(
    workspace_id: UUID,
    question_id: UUID,
    payload: MarketQuestionDependencyCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = (
        f"POST /v1/market/workspaces/{workspace_id}/questions/{question_id}/dependencies"
    )
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={
            "workspace_id": str(workspace_id),
            "question_id": str(question_id),
            "depends_on_question_id": str(payload.depends_on_question_id),
        },
    )
    if (replay := guard.begin()) is not None:
        return replay
    edge, reused = market_research_graph_service.create_question_dependency(
        session,
        context.organization_id,
        workspace_id,
        question_id,
        payload.depends_on_question_id,
        actor_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_200_OK if reused else status.HTTP_201_CREATED,
        body=MarketQuestionDependencyRead.model_validate(edge),
    )


@router.delete(
    "/market/workspaces/{workspace_id}/questions/{question_id}/dependencies/{dependency_id}",
    response_model=MarketQuestionDependencyDeleted,
    summary="移除研究问题依赖",
)
def delete_market_question_dependency(
    workspace_id: UUID,
    question_id: UUID,
    dependency_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = (
        f"DELETE /v1/market/workspaces/{workspace_id}/questions/{question_id}/"
        f"dependencies/{dependency_id}"
    )
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={
            "workspace_id": str(workspace_id),
            "question_id": str(question_id),
            "dependency_id": str(dependency_id),
        },
    )
    if (replay := guard.begin()) is not None:
        return replay
    deleted_id = market_research_graph_service.delete_question_dependency(
        session,
        context.organization_id,
        workspace_id,
        question_id,
        dependency_id,
        actor_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_200_OK,
        body=MarketQuestionDependencyDeleted(dependency_id=deleted_id, deleted=True),
    )


@router.post(
    "/market/workspaces/{workspace_id}/questions/{question_id}/answers",
    response_model=MarketQuestionAnswerRead,
    status_code=status.HTTP_201_CREATED,
    summary="创建带证据链的研究问题答案版本",
)
def create_market_question_answer(
    workspace_id: UUID,
    question_id: UUID,
    payload: MarketQuestionAnswerCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = (
        f"POST /v1/market/workspaces/{workspace_id}/questions/{question_id}/answers"
    )
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={
            "workspace_id": str(workspace_id),
            "question_id": str(question_id),
            **payload.model_dump(mode="json"),
        },
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_question_answer_service.create_answer(
        session,
        context.organization_id,
        workspace_id,
        question_id,
        answer_summary=payload.answer_summary,
        assumptions=payload.assumptions,
        unknowns=payload.unknowns,
        confidence_level=payload.confidence_level,
        generated_by_run_id=payload.generated_by_run_id,
        evidence=[item.model_dump() for item in payload.evidence],
        evidence_gap=payload.evidence_gap,
        created_by_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_201_CREATED,
        body=MarketQuestionAnswerRead.model_validate(row),
    )


@router.post(
    "/market/workspaces/{workspace_id}/questions/{question_id}:draft-answer",
    response_model=MarketQuestionAnswerGenerationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="启动研究问题答案草拟 Agent",
)
def enqueue_market_question_answer_draft(
    workspace_id: UUID,
    question_id: UUID,
    payload: MarketQuestionAnswerGenerationCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = (
        f"POST /v1/market/workspaces/{workspace_id}/questions/{question_id}:draft-answer"
    )
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={
            "workspace_id": str(workspace_id),
            "question_id": str(question_id),
            **payload.model_dump(mode="json"),
        },
    )
    if (replay := guard.begin()) is not None:
        return replay
    result = market_question_answer_agent_service.enqueue_answer_draft(
        session,
        context.organization_id,
        workspace_id,
        question_id,
        evidence_ids=payload.evidence_ids,
        created_by_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_202_ACCEPTED,
        body=MarketQuestionAnswerGenerationAccepted.model_validate(result),
    )


@router.get(
    "/market/workspaces/{workspace_id}/questions/{question_id}/answers",
    response_model=list[MarketQuestionAnswerRead],
    summary="读取研究问题的全部答案版本",
)
def list_market_question_answers(
    workspace_id: UUID,
    question_id: UUID,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_VIEWER)
    rows = market_question_answer_service.list_answers(
        session, context.organization_id, workspace_id, question_id
    )
    return [MarketQuestionAnswerRead.model_validate(row) for row in rows]


def _review_market_question_answer(
    decision: str,
    workspace_id: UUID,
    question_id: UUID,
    answer_id: UUID,
    payload: MarketQuestionAnswerReview,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_ADMIN)
    action = "accept" if decision == "ACCEPTED" else "reject"
    endpoint = (
        f"POST /v1/market/workspaces/{workspace_id}/questions/{question_id}/answers/"
        f"{answer_id}:{action}"
    )
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={
            "workspace_id": str(workspace_id),
            "question_id": str(question_id),
            "answer_id": str(answer_id),
            "review_note": payload.review_note,
        },
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_question_answer_service.review_answer(
        session,
        context.organization_id,
        workspace_id,
        question_id,
        answer_id,
        decision=decision,
        actor_user_id=context.user_id,
        review_note=payload.review_note,
    )
    return guard.finish(
        status_code=status.HTTP_200_OK,
        body=MarketQuestionAnswerRead.model_validate(row),
    )


@router.post(
    "/market/workspaces/{workspace_id}/questions/{question_id}/answers/{answer_id}:accept",
    response_model=MarketQuestionAnswerRead,
    summary="人工采纳研究问题答案",
)
def accept_market_question_answer(
    workspace_id: UUID,
    question_id: UUID,
    answer_id: UUID,
    payload: MarketQuestionAnswerReview,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    return _review_market_question_answer(
        "ACCEPTED",
        workspace_id,
        question_id,
        answer_id,
        payload,
        context,
        session,
        idempotency_key,
    )


@router.post(
    "/market/workspaces/{workspace_id}/questions/{question_id}/answers/{answer_id}:reject",
    response_model=MarketQuestionAnswerRead,
    summary="人工驳回研究问题答案",
)
def reject_market_question_answer(
    workspace_id: UUID,
    question_id: UUID,
    answer_id: UUID,
    payload: MarketQuestionAnswerReview,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    return _review_market_question_answer(
        "REJECTED",
        workspace_id,
        question_id,
        answer_id,
        payload,
        context,
        session,
        idempotency_key,
    )



@router.post(
    "/market/workspaces/{workspace_id}/answers/{answer_id}:verify-claims",
    response_model=MarketClaimVerificationAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="启动答案版本的原子主张核验 Agent",
)
def enqueue_market_claim_verification(
    workspace_id: UUID,
    answer_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = (
        f"POST /v1/market/workspaces/{workspace_id}/answers/{answer_id}:verify-claims"
    )
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), "answer_id": str(answer_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay
    result = market_claim_verification_service.enqueue_claim_verification(
        session,
        context.organization_id,
        workspace_id,
        answer_id,
        created_by_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_202_ACCEPTED,
        body=MarketClaimVerificationAccepted.model_validate(result),
    )


@router.get(
    "/market/workspaces/{workspace_id}/claim-verification-runs/{verification_run_id}",
    response_model=MarketClaimVerificationRunRead,
    summary="读取主张核验运行结果及原子主张",
)
def get_market_claim_verification_run(
    workspace_id: UUID,
    verification_run_id: UUID,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_VIEWER)
    result = market_claim_verification_service.get_verification_run(
        session,
        context.organization_id,
        workspace_id,
        verification_run_id,
    )
    return MarketClaimVerificationRunRead.model_validate(result)


@router.get(
    "/market/workspaces/{workspace_id}/claims",
    response_model=Page[MarketClaimRead],
    summary="读取 Workspace 的原子研究主张",
)
def list_market_claims(
    workspace_id: UUID,
    context: ContextDep,
    session: SessionDep,
    answer_id: UUID | None = ANSWER_ID_QUERY,
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None),
):
    require_role(context, ROLE_VIEWER)
    page_size = clamp_limit(limit)
    offset = decode_cursor(cursor)
    rows = market_claim_verification_service.list_claims(
        session,
        context.organization_id,
        workspace_id,
        answer_id=answer_id,
        limit=page_size,
        offset=offset,
    )
    return Page[MarketClaimRead](
        items=[MarketClaimRead.model_validate(row) for row in rows],
        next_cursor=page_cursor(rows, offset, page_size),
    )


def _review_market_claim(
    decision: str,
    workspace_id: UUID,
    claim_id: UUID,
    payload: MarketClaimReview,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_ADMIN)
    action = "accept" if decision == "ACCEPTED" else "reject"
    endpoint = (
        f"POST /v1/market/workspaces/{workspace_id}/claims/{claim_id}:{action}"
    )
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={
            "workspace_id": str(workspace_id),
            "claim_id": str(claim_id),
            "review_note": payload.review_note,
        },
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_claim_verification_service.review_claim(
        session,
        context.organization_id,
        workspace_id,
        claim_id,
        decision=decision,
        actor_user_id=context.user_id,
        review_note=payload.review_note,
    )
    return guard.finish(
        status_code=status.HTTP_200_OK,
        body=MarketClaimRead.model_validate(row),
    )


@router.post(
    "/market/workspaces/{workspace_id}/claims/{claim_id}:accept",
    response_model=MarketClaimRead,
    summary="人工采纳一条原子研究主张",
)
def accept_market_claim(
    workspace_id: UUID,
    claim_id: UUID,
    payload: MarketClaimReview,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    return _review_market_claim(
        "ACCEPTED",
        workspace_id,
        claim_id,
        payload,
        context,
        session,
        idempotency_key,
    )


@router.post(
    "/market/workspaces/{workspace_id}/claims/{claim_id}:reject",
    response_model=MarketClaimRead,
    summary="人工驳回一条原子研究主张",
)
def reject_market_claim(
    workspace_id: UUID,
    claim_id: UUID,
    payload: MarketClaimReview,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    return _review_market_claim(
        "REJECTED",
        workspace_id,
        claim_id,
        payload,
        context,
        session,
        idempotency_key,
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
        verification_status="UNVERIFIED",
        quality=None,
        payload=payload.payload,
        actor_user_id=context.user_id,
    )
    market_research_dag_service.wake_workspace_executions(
        session, context.organization_id, workspace_id
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



@router.get(
    "/market/workspaces/{workspace_id}/evidence-intelligence",
    response_model=MarketEvidenceIntelligenceRead,
    summary="读取工作空间证据质量与来源关系报告",
)
def get_market_evidence_intelligence(
    workspace_id: UUID,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_VIEWER)
    report = market_evidence_intelligence_service.build_report(
        session, context.organization_id, workspace_id
    )
    return MarketEvidenceIntelligenceRead.model_validate(report)


@router.get(
    "/market/workspaces/{workspace_id}/source-verification-plan",
    response_model=MarketSourceVerificationPlanRead,
    summary="读取主张级来源核验计划",
)
def get_market_source_verification_plan(
    workspace_id: UUID,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_VIEWER)
    plan = market_source_verification_service.build_plan(
        session, context.organization_id, workspace_id
    )
    return MarketSourceVerificationPlanRead.model_validate(plan)


@router.post(
    "/market/workspaces/{workspace_id}/evidence/{evidence_id}:extract",
    response_model=MarketSourceExtractionAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="提取用户选定搜索来源的网页正文",
)
def enqueue_market_source_extraction(
    workspace_id: UUID,
    evidence_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = (
        f"POST /v1/market/workspaces/{workspace_id}/evidence/{evidence_id}:extract"
    )
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), "evidence_id": str(evidence_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay
    result = market_source_extraction_service.enqueue_page_extraction(
        session,
        context.organization_id,
        workspace_id,
        evidence_id,
        created_by_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_202_ACCEPTED,
        body=MarketSourceExtractionAccepted.model_validate(result),
    )


@router.get(
    "/market/workspaces/{workspace_id}/source-extractions/{workflow_id}",
    response_model=MarketSourceExtractionRead,
    summary="读取网页正文提取任务状态",
)
def get_market_source_extraction(
    workspace_id: UUID,
    workflow_id: UUID,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_VIEWER)
    return MarketSourceExtractionRead.model_validate(
        market_source_extraction_service.get_page_extraction(
            session, context.organization_id, workspace_id, workflow_id
        )
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


@router.post(
    "/market/workspaces/{workspace_id}/opportunities",
    response_model=MarketOpportunityRead,
    status_code=status.HTTP_201_CREATED,
    summary="创建市场商业机会候选",
)
def create_market_opportunity(
    workspace_id: UUID,
    payload: MarketOpportunityCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = f"POST /v1/market/workspaces/{workspace_id}/opportunities"
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_opportunity_service.create_opportunity(
        session,
        context.organization_id,
        workspace_id,
        title=payload.title,
        description=payload.description,
        next_action=payload.next_action,
        owner_user_id=payload.owner_user_id,
        created_by_run_id=payload.created_by_run_id,
        created_by_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_201_CREATED,
        body=MarketOpportunityRead.model_validate(row),
    )


@router.get(
    "/market/workspaces/{workspace_id}/opportunities",
    response_model=Page[MarketOpportunityRead],
    summary="读取 Workspace 商业机会",
)
def list_market_opportunities(
    workspace_id: UUID,
    context: ContextDep,
    session: SessionDep,
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None),
    status_filter: MarketOpportunityStatus | None = OPPORTUNITY_STATUS_QUERY,
):
    require_role(context, ROLE_VIEWER)
    page_size = clamp_limit(limit)
    offset = decode_cursor(cursor)
    rows = market_opportunity_service.list_opportunities(
        session,
        context.organization_id,
        workspace_id,
        status_filter=status_filter,
        limit=page_size,
        offset=offset,
    )
    return Page[MarketOpportunityRead](
        items=[MarketOpportunityRead.model_validate(row) for row in rows],
        next_cursor=page_cursor(rows, offset, page_size),
    )


@router.get(
    "/market/workspaces/{workspace_id}/opportunities/{opportunity_id}",
    response_model=MarketOpportunityDetailRead,
    summary="读取商业机会详情、证据、实体和评估历史",
)
def get_market_opportunity(
    workspace_id: UUID,
    opportunity_id: UUID,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_VIEWER)
    result = market_opportunity_service.get_opportunity_detail(
        session, context.organization_id, workspace_id, opportunity_id
    )
    return MarketOpportunityDetailRead.model_validate(result)


@router.post(
    "/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/evidence",
    response_model=MarketOpportunityEvidenceRead,
    status_code=status.HTTP_201_CREATED,
    summary="为商业机会关联支持、反驳或上下文证据",
)
def add_market_opportunity_evidence(
    workspace_id: UUID,
    opportunity_id: UUID,
    payload: MarketOpportunityEvidenceCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = (
        f"POST /v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/evidence"
    )
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={
            "workspace_id": str(workspace_id),
            "opportunity_id": str(opportunity_id),
            **payload.model_dump(mode="json"),
        },
    )
    if (replay := guard.begin()) is not None:
        return replay
    result = market_opportunity_service.add_opportunity_evidence(
        session,
        context.organization_id,
        workspace_id,
        opportunity_id,
        evidence_id=payload.evidence_id,
        relation_type=payload.relation_type,
        rationale=payload.rationale,
        actor_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_201_CREATED,
        body=MarketOpportunityEvidenceRead.model_validate(result),
    )


@router.post(
    "/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/entities",
    response_model=list[MarketEntityRead],
    status_code=status.HTTP_201_CREATED,
    summary="将当前 Workspace 候选实体关联到商业机会",
)
def attach_market_opportunity_entities(
    workspace_id: UUID,
    opportunity_id: UUID,
    payload: MarketOpportunityEntitiesAttach,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = (
        f"POST /v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/entities"
    )
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={
            "workspace_id": str(workspace_id),
            "opportunity_id": str(opportunity_id),
            **payload.model_dump(mode="json"),
        },
    )
    if (replay := guard.begin()) is not None:
        return replay
    rows = market_opportunity_service.attach_opportunity_entities(
        session,
        context.organization_id,
        workspace_id,
        opportunity_id,
        entity_ids=payload.entity_ids,
        relationship_note=payload.relationship_note,
        actor_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_201_CREATED,
        body=[MarketEntityRead.model_validate(row) for row in rows],
    )


@router.post(
    "/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:assess",
    response_model=MarketOpportunityAssessmentRead,
    status_code=status.HTTP_201_CREATED,
    summary="追加一版商业机会评估快照",
)
def assess_market_opportunity(
    workspace_id: UUID,
    opportunity_id: UUID,
    payload: MarketOpportunityAssessmentCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = (
        f"POST /v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:assess"
    )
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={
            "workspace_id": str(workspace_id),
            "opportunity_id": str(opportunity_id),
            **payload.model_dump(mode="json"),
        },
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_opportunity_service.assess_opportunity(
        session,
        context.organization_id,
        workspace_id,
        opportunity_id,
        dimensions=payload.dimensions,
        summary=payload.summary,
        confidence_level=payload.confidence_level,
        assessed_by_run_id=payload.assessed_by_run_id,
        assessed_by_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_201_CREATED,
        body=MarketOpportunityAssessmentRead.model_validate(row),
    )


@router.post(
    "/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:transition",
    response_model=MarketOpportunityRead,
    summary="按服务端状态机推进或退回商业机会",
)
def transition_market_opportunity(
    workspace_id: UUID,
    opportunity_id: UUID,
    payload: MarketOpportunityTransition,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = (
        f"POST /v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:transition"
    )
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={
            "workspace_id": str(workspace_id),
            "opportunity_id": str(opportunity_id),
            **payload.model_dump(mode="json"),
        },
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_opportunity_service.transition_opportunity(
        session,
        context.organization_id,
        workspace_id,
        opportunity_id,
        target_status=payload.target_status,
        note=payload.note,
        actor_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_200_OK,
        body=MarketOpportunityRead.model_validate(row),
    )


@router.post(
    "/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:qualify",
    response_model=MarketOpportunityRead,
    summary="人工确认商业机会资格",
)
def qualify_market_opportunity(
    workspace_id: UUID,
    opportunity_id: UUID,
    payload: MarketOpportunityQualify,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_ADMIN)
    endpoint = (
        f"POST /v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:qualify"
    )
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={
            "workspace_id": str(workspace_id),
            "opportunity_id": str(opportunity_id),
            **payload.model_dump(mode="json"),
        },
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_opportunity_service.qualify_opportunity(
        session,
        context.organization_id,
        workspace_id,
        opportunity_id,
        qualification_reason=payload.qualification_reason,
        actor_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_200_OK,
        body=MarketOpportunityRead.model_validate(row),
    )


@router.post(
    "/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:outcome",
    response_model=MarketOpportunityRead,
    summary="记录商业机会 WON/LOST 的实际业务结果",
)
def record_market_opportunity_outcome(
    workspace_id: UUID,
    opportunity_id: UUID,
    payload: MarketOpportunityOutcome,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_ADMIN)
    endpoint = (
        f"POST /v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:outcome"
    )
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={
            "workspace_id": str(workspace_id),
            "opportunity_id": str(opportunity_id),
            **payload.model_dump(mode="json"),
        },
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_opportunity_service.record_opportunity_outcome(
        session,
        context.organization_id,
        workspace_id,
        opportunity_id,
        outcome=payload.outcome,
        outcome_note=payload.outcome_note,
        actor_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_200_OK,
        body=MarketOpportunityRead.model_validate(row),
    )


@router.get(
    "/market/workspaces/{workspace_id}/trips",
    response_model=list[MarketWorkspaceTripLinkRead],
    summary="读取工作空间关联的出差任务",
)
def list_workspace_trips(
    workspace_id: UUID,
    context: ContextDep,
    session: SessionDep,
    include_unlinked: bool = Query(default=False),
):
    require_role(context, ROLE_VIEWER)
    rows = market_opportunity_service.list_workspace_trips(
        session, context.organization_id, workspace_id, linked_only=not include_unlinked
    )
    return [MarketWorkspaceTripLinkRead.model_validate(row) for row in rows]


@router.post(
    "/market/workspaces/{workspace_id}/trips",
    response_model=MarketWorkspaceTripLinkRead,
    status_code=status.HTTP_201_CREATED,
    summary="关联现有 Trip 到市场工作空间",
)
def link_trip_to_workspace(
    workspace_id: UUID,
    payload: MarketWorkspaceTripLinkCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = f"POST /v1/market/workspaces/{workspace_id}/trips"
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_opportunity_service.link_trip_to_workspace(
        session,
        context.organization_id,
        workspace_id,
        payload.trip_id,
        link_note=payload.link_note,
        actor_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_201_CREATED,
        body=MarketWorkspaceTripLinkRead.model_validate(row),
    )


@router.delete(
    "/market/workspaces/{workspace_id}/trips/{trip_id}",
    response_model=MarketWorkspaceTripLinkRead,
    summary="解除 Workspace 与 Trip 关联并保留历史",
)
def unlink_trip_from_workspace(
    workspace_id: UUID,
    trip_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    endpoint = f"DELETE /v1/market/workspaces/{workspace_id}/trips/{trip_id}"
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"workspace_id": str(workspace_id), "trip_id": str(trip_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = market_opportunity_service.unlink_trip_from_workspace(
        session,
        context.organization_id,
        workspace_id,
        trip_id,
        actor_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_200_OK,
        body=MarketWorkspaceTripLinkRead.model_validate(row),
    )


@router.get(
    "/market/workspaces/{workspace_id}/research-plans",
    response_model=list[ResearchPlanRead],
    summary="读取工作空间关联的研究计划，可按目标问题筛选",
)
def list_workspace_research_plans(
    workspace_id: UUID,
    context: ContextDep,
    session: SessionDep,
    target_question_id: UUID | None = TARGET_QUESTION_ID_QUERY,
):
    require_role(context, ROLE_VIEWER)
    rows = research_service.list_research_plans_for_workspace(
        session, context.organization_id, workspace_id
    )
    if target_question_id is not None:
        rows = [row for row in rows if row.target_question_id == target_question_id]
    return rows


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
