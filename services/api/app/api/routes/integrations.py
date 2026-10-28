"""外部信号接入接口（设计文档第 14 章）。

对接真实供应商时，由集成层把供应商回调/轮询结果转换成这里的信号结构；本接口不区分供应商
实现，只负责校验、幂等落库。信号本身不会直接变成用户建议——那一步由主动式运行时按出行
上下文判断，确保"外部变化"与"这次出行是否相关"分开。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import ROLE_ADMIN, ROLE_VIEWER, ContextDep, SessionDep, require_role
from app.api.schemas import ExternalSignalCreate, ExternalSignalRead
from app.services import external_service

router = APIRouter(tags=["integrations"])


@router.post(
    "/integrations/signals",
    response_model=ExternalSignalRead,
    status_code=status.HTTP_201_CREATED,
    summary="接入外部信号（供应商回调或轮询结果）",
)
def ingest_signal(
    payload: ExternalSignalCreate, context: ContextDep, session: SessionDep
):
    require_role(context, ROLE_ADMIN)
    signal = external_service.ingest(session, context.organization_id, payload)
    return ExternalSignalRead.model_validate(signal)


@router.get(
    "/integrations/signals",
    response_model=list[ExternalSignalRead],
    summary="外部信号列表（按观察时间倒序）",
)
def list_signals(
    context: ContextDep,
    session: SessionDep,
    subject_ref: Annotated[str | None, Query(description="按国家代码或出行 id 过滤")] = None,
    signal_type: Annotated[str | None, Query(description="按信号类型过滤")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
):
    require_role(context, ROLE_VIEWER)
    signals = external_service.list_signals(
        session,
        context.organization_id,
        subject_ref=subject_ref,
        signal_type=signal_type,
        limit=limit,
    )
    return [ExternalSignalRead.model_validate(item) for item in signals]
