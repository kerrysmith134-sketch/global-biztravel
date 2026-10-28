"""外部信号接入接口（设计文档第 14 章）。

对接真实供应商时，由集成层把供应商回调/轮询结果转换成这里的信号结构；本接口不区分供应商
实现，只负责校验、幂等落库。信号本身不会直接变成用户建议——那一步由主动式运行时按出行
上下文判断，确保"外部变化"与"这次出行是否相关"分开。
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.deps import ROLE_ADMIN, ROLE_VIEWER, ContextDep, SessionDep, require_role
from app.api.schemas import (
    ExternalSignalCreate,
    ExternalSignalRead,
    IntegrationConnectionCreate,
    IntegrationConnectionRead,
)
from app.models.workflow import IntegrationConnection
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


@router.get(
    "/integrations/connections",
    response_model=list[IntegrationConnectionRead],
    summary="外部集成连接与能力状态",
)
def list_connections(context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    rows = session.scalars(
        select(IntegrationConnection)
        .where(IntegrationConnection.organization_id == str(context.organization_id))
        .order_by(IntegrationConnection.provider, IntegrationConnection.capability)
    )
    return [IntegrationConnectionRead.model_validate(row) for row in rows]


@router.post(
    "/integrations/connections",
    response_model=IntegrationConnectionRead,
    status_code=status.HTTP_201_CREATED,
    summary="注册/更新外部集成连接（ADMIN）",
)
def upsert_connection(
    payload: IntegrationConnectionCreate, context: ContextDep, session: SessionDep
):
    require_role(context, ROLE_ADMIN)
    statement = (
        pg_insert(IntegrationConnection.__table__)
        .values(
            organization_id=str(context.organization_id),
            provider=payload.provider,
            capability=payload.capability,
            status=payload.status,
            config=payload.config,
        )
        .on_conflict_do_update(
            constraint="uq_integration_connections_org_provider_capability",
            set_={"status": payload.status, "config": payload.config},
        )
        .returning(IntegrationConnection.__table__.c.id)
    )
    connection_id = session.execute(statement).scalar_one()
    row = session.get(IntegrationConnection, connection_id)
    return IntegrationConnectionRead.model_validate(row)


@router.delete(
    "/integrations/connections/{connection_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除外部集成连接（ADMIN）",
)
def delete_connection(connection_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_ADMIN)
    session.execute(
        delete(IntegrationConnection).where(
            IntegrationConnection.organization_id == str(context.organization_id),
            IntegrationConnection.id == str(connection_id),
        )
    )
