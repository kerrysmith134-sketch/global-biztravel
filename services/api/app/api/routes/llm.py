"""LLM 工具目录与调用审计接口（设计文档第 7 章）。

- ``GET /v1/llm/tools``：列出 Agent 被允许调用的工具及其权限域/人审要求。工具目录是代码里的
  声明式常量，不是数据库配置——能力边界必须随代码评审走。
- ``GET /v1/llm/invocations``：按组织查调用记录（仅 ADMIN），用于回答"这条产出由哪个模型、
  哪版 prompt、多少成本产生"。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import ROLE_ADMIN, ROLE_VIEWER, ContextDep, SessionDep, require_role
from app.api.schemas import LLMInvocationRead, ToolRead
from app.llm.tools import list_tools
from app.models.llm import LLMInvocation

router = APIRouter(tags=["llm"])


@router.get("/llm/tools", response_model=list[ToolRead], summary="Agent 工具契约目录")
def get_tools(context: ContextDep):
    require_role(context, ROLE_VIEWER)
    return [ToolRead.model_validate(item) for item in list_tools()]


@router.get(
    "/llm/invocations",
    response_model=list[LLMInvocationRead],
    summary="LLM 调用记录（审计，最新在前）",
)
def list_invocations(
    context: ContextDep,
    session: SessionDep,
    task: Annotated[str | None, Query(description="按任务过滤")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    require_role(context, ROLE_ADMIN)
    stmt = select(LLMInvocation).where(
        LLMInvocation.organization_id == str(context.organization_id)
    )
    if task:
        stmt = stmt.where(LLMInvocation.task == task)
    rows = session.scalars(
        stmt.order_by(LLMInvocation.created_at.desc(), LLMInvocation.id).limit(limit)
    )
    return [
        LLMInvocationRead(
            id=row.id,
            task=row.task,
            model=row.model,
            prompt_template_id=row.prompt_template_id,
            prompt_version=row.prompt_version,
            tokens_in=row.tokens_in,
            tokens_out=row.tokens_out,
            latency_ms=row.latency_ms,
            cost_estimate=float(row.cost_estimate),
            degraded=row.degraded,
            cached=row.cached,
            error=row.error,
            trace_id=row.trace_id,
            created_at=row.created_at,
        )
        for row in rows
    ]
