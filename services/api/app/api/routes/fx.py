"""组织汇率接口（PRD 方向六：显式汇率，不做假换算）。"""

from __future__ import annotations

from fastapi import APIRouter, Query, status

from app.api.deps import (
    ROLE_ADMIN,
    ROLE_VIEWER,
    ContextDep,
    IdempotencyKeyDep,
    SessionDep,
    require_role,
)
from app.api.idempotent import IdempotentWrite
from app.api.schemas import FxRateCreate, FxRateRead, FxSyncRead, FxSyncRequest
from app.services import fx_service, job_service

router = APIRouter(tags=["fx"])


@router.post(
    "/fx-rates",
    response_model=FxRateRead,
    status_code=status.HTTP_201_CREATED,
    summary="录入/更新一条汇率快照（同一时间点幂等覆盖）",
)
def create_fx_rate(
    payload: FxRateCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_ADMIN)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint="POST /v1/fx-rates",
        key=idempotency_key,
        payload={
            "base_currency": payload.base_currency,
            "quote_currency": payload.quote_currency,
            "rate": str(payload.rate),
            "as_of": payload.as_of.isoformat() if payload.as_of else None,
            "source": payload.source,
        },
    )
    if (replay := guard.begin()) is not None:
        return replay
    row = fx_service.upsert_rate(
        session,
        context.organization_id,
        base_currency=payload.base_currency,
        quote_currency=payload.quote_currency,
        rate=payload.rate,
        as_of=payload.as_of,
        source=payload.source,
        actor_user_id=context.user_id,
    )
    return guard.finish(
        status_code=status.HTTP_201_CREATED,
        body=FxRateRead.model_validate(row),
    )


@router.get(
    "/fx-rates",
    response_model=list[FxRateRead],
    summary="每个币种对的最新汇率",
)
def list_fx_rates(
    context: ContextDep,
    session: SessionDep,
    base_currency: str | None = Query(default=None, min_length=3, max_length=3),
    quote_currency: str | None = Query(default=None, min_length=3, max_length=3),
):
    require_role(context, ROLE_VIEWER)
    return [
        FxRateRead.model_validate(row)
        for row in fx_service.latest_rates(
            session,
            context.organization_id,
            base_currency=base_currency,
            quote_currency=quote_currency,
        )
    ]


FX_SYNC_ENDPOINT = "POST /v1/fx-rates:sync"


@router.post(
    "/fx-rates:sync",
    response_model=FxSyncRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="从公开行情源同步最新汇率（异步 job；未配置源时任务显式失败）",
)
def sync_fx_rates(
    payload: FxSyncRequest,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    """ADR-0033：行情写入同一张 `fx_rates` 表（``source=fx-<provider>``）。

    受理即返回 202；真正取数由 Worker 执行 ``fx_rate_sync`` 任务。行情源未配置
    时任务终态 FAILED 并带原因——不伪造数据。幂等键回放受理响应；同一
    ``(org, base, symbols)`` 的在途任务按 dedupe 复用。
    """
    require_role(context, ROLE_ADMIN)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=FX_SYNC_ENDPOINT,
        key=idempotency_key,
        payload={
            "base_currency": payload.base_currency,
            "symbols": payload.symbols,
        },
    )
    if (replay := guard.begin()) is not None:
        return replay

    symbols = sorted({code.strip().upper() for code in payload.symbols})
    dedupe_key = f"fx_sync:{payload.base_currency.upper()}:{','.join(symbols)}"
    in_flight = job_service.find_inflight(session, context.organization_id, dedupe_key)

    job = job_service.enqueue(
        session,
        context.organization_id,
        job_type=job_service.JOB_TYPE_FX_SYNC,
        payload={"base_currency": payload.base_currency.upper(), "symbols": symbols},
        dedupe_key=dedupe_key,
        created_by_user_id=context.user_id,
    )

    body = FxSyncRead(
        job_id=job.id,
        status=job.status,
        base_currency=payload.base_currency.upper(),
        symbols=symbols,
        reused=in_flight is not None,
    )
    return guard.finish(status_code=status.HTTP_202_ACCEPTED, body=body)
