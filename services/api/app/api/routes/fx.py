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
from app.api.schemas import FxRateCreate, FxRateRead
from app.services import fx_service

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
