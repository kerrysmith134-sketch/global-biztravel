"""User-intent-driven research planning endpoints.

Planning is a distinct step from evidence retrieval. A successful POST only means
that a user-scoped plan has been created; no external source is claimed as retrieved.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.api.deps import (
    ROLE_TRAVELER,
    ROLE_VIEWER,
    ContextDep,
    IdempotencyKeyDep,
    SessionDep,
    require_role,
)
from app.api.idempotent import IdempotentWrite
from app.api.schemas import (
    ResearchExecuteAccepted,
    ResearchExecuteCreate,
    ResearchPlanCreate,
    ResearchPlanRead,
)
from app.services import research_service

router = APIRouter(tags=["research"])
CREATE_PLAN_ENDPOINT = "POST /v1/research/plans"


@router.post(
    "/research/plans",
    response_model=ResearchPlanRead,
    status_code=status.HTTP_201_CREATED,
    summary="根据用户意图生成研究计划（不执行检索）",
)
def create_plan(
    payload: ResearchPlanCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=CREATE_PLAN_ENDPOINT,
        key=idempotency_key,
        payload=payload.model_dump(mode="json"),
    )
    if (replay := guard.begin()) is not None:
        return replay
    plan = research_service.create_research_plan(
        session,
        context.organization_id,
        payload,
        user_id=context.user_id,
    )
    return guard.finish(status_code=status.HTTP_201_CREATED, body=plan)


@router.post(
    "/research/plans/{plan_id}:execute",
    response_model=ResearchExecuteAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="启动已审阅研究计划的外部检索（异步）",
)
def execute_plan(
    plan_id: UUID,
    payload: ResearchExecuteCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=f"POST /v1/research/plans/{plan_id}:execute",
        key=idempotency_key,
        payload={
            "plan_id": str(plan_id),
            "search_queries": payload.search_queries,
        },
    )
    if (replay := guard.begin()) is not None:
        return replay
    body = research_service.enqueue_research_execution(
        session,
        context.organization_id,
        plan_id,
        reviewed_search_queries=payload.search_queries,
        user_id=context.user_id,
    )
    return guard.finish(status_code=status.HTTP_202_ACCEPTED, body=body)


@router.get(
    "/research/plans/{plan_id}",
    response_model=ResearchPlanRead,
    summary="读取研究计划、运行审计与证据来源",
)
def get_plan(plan_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    return research_service.get_research_plan(session, context.organization_id, plan_id)
