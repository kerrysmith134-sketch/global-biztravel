"""出行（Trip）与出行内拜访的 HTTP 接口。

路由只做三件事：解析请求、调用服务层用例、组装响应。**业务规则不在这一层**——例如
"哪些状态能由客户端直接设置"住在 :mod:`app.services.trip_service`，"换了内容就换版本号"
住在同一模块的 ``touch_trip``。这样同一套规则可以被导入、后台任务和未来的 Agent 工具复用。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy.orm import Session

from app.api.deps import (
    ROLE_TRAVELER,
    ROLE_VIEWER,
    ContextDep,
    IdempotencyKeyDep,
    IfMatchDep,
    SessionDep,
    require_role,
)
from app.api.idempotent import IdempotentWrite
from app.api.pagination import clamp_limit, decode_cursor
from app.api.pagination import next_cursor as page_cursor
from app.api.schemas import (
    Page,
    TripCloseoutReadinessRead,
    TripCreate,
    TripOutcomeRead,
    TripRead,
    TripUpdate,
    VisitCreate,
    VisitRead,
)
from app.models.trip import Trip
from app.services import trip_closeout_service, trip_outcome_service, trip_service, visit_service

router = APIRouter(tags=["trips"])

TRIPS_ENDPOINT = "POST /v1/trips"
VISITS_ENDPOINT = "POST /v1/trips/{trip_id}/visits"
TRIP_HOLD_ENDPOINT = "POST /v1/trips/{trip_id}/hold"
TRIP_RESUME_ENDPOINT = "POST /v1/trips/{trip_id}/resume"
TRIP_COMPLETE_ENDPOINT = "POST /v1/trips/{trip_id}/complete"


def trip_read(
    session: Session,
    trip: Trip,
    *,
    visit_count: int | None = None,
    customer_count: int | None = None,
    plan_version_count: int | None = None,
) -> TripRead:
    """把 Trip ORM 对象转成读模型，并补齐列表页需要的计数。"""
    read = TripRead.model_validate(trip)
    read.visit_count = (
        trip_service.visit_count(session, trip.id) if visit_count is None else visit_count
    )
    read.customer_count = (
        trip_service.customer_counts(session, [trip.id]).get(trip.id, 0)
        if customer_count is None
        else customer_count
    )
    read.plan_version_count = (
        trip_service.plan_version_counts(session, [trip.id]).get(trip.id, 0)
        if plan_version_count is None
        else plan_version_count
    )
    return read


@router.post(
    "/trips",
    response_model=TripRead,
    status_code=status.HTTP_201_CREATED,
    summary="创建出行",
)
def create_trip(
    payload: TripCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=TRIPS_ENDPOINT,
        key=idempotency_key,
        payload=payload.model_dump(mode="json"),
    )
    # 幂等检查必须在建 Trip 之前：否则重放请求会再建一条，只是把首次响应回放给你。
    if (replay := guard.begin()) is not None:
        return replay
    trip = trip_service.create_trip(
        session, context.organization_id, payload, owner_user_id=context.user_id
    )
    body = trip_read(session, trip, visit_count=0)
    return guard.finish(status_code=status.HTTP_201_CREATED, body=body)


@router.get("/trips", response_model=Page[TripRead], summary="出行列表")
def list_trips(
    context: ContextDep,
    session: SessionDep,
    trip_status: str | None = Query(default=None, alias="status"),
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None),
):
    require_role(context, ROLE_VIEWER)
    page_size = clamp_limit(limit)
    offset = decode_cursor(cursor)
    trips = trip_service.list_trips(
        session, context.organization_id, status=trip_status, limit=page_size, offset=offset
    )
    trip_ids = [trip.id for trip in trips]
    counts = trip_service.visit_counts(session, trip_ids)
    customers = trip_service.customer_counts(session, trip_ids)
    plans = trip_service.plan_version_counts(session, trip_ids)
    items = [
        trip_read(
            session,
            trip,
            visit_count=counts.get(trip.id, 0),
            customer_count=customers.get(trip.id, 0),
            plan_version_count=plans.get(trip.id, 0),
        )
        for trip in trips
    ]
    return Page[TripRead](items=items, next_cursor=page_cursor(trips, offset, page_size))


@router.get("/trips/{trip_id}", response_model=TripRead, summary="出行详情")
def get_trip(trip_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    trip = trip_service.require_trip(session, context.organization_id, trip_id)
    return trip_read(session, trip)


@router.patch("/trips/{trip_id}", response_model=TripRead, summary="更新出行")
def update_trip(
    trip_id: UUID,
    payload: TripUpdate,
    context: ContextDep,
    session: SessionDep,
    if_match: IfMatchDep,
):
    """更新出行。可带 ``If-Match`` 做乐观并发：版本不符返回 412，提示客户端重取。"""
    require_role(context, ROLE_TRAVELER)
    trip = trip_service.update_trip(
        session,
        context.organization_id,
        trip_id,
        payload,
        expected_version=if_match,
    )
    return trip_read(session, trip)


@router.get(
    "/trips/{trip_id}/visits",
    response_model=list[VisitRead],
    summary="出行下的拜访列表",
)
def list_trip_visits(
    trip_id: UUID,
    context: ContextDep,
    session: SessionDep,
    visit_status: str | None = Query(default=None, alias="status"),
):
    require_role(context, ROLE_VIEWER)
    trip_service.require_trip(session, context.organization_id, trip_id)
    return visit_service.list_visits(
        session, context.organization_id, trip_id, status=visit_status
    )


@router.post(
    "/trips/{trip_id}/visits",
    response_model=VisitRead,
    status_code=status.HTTP_201_CREATED,
    summary="在出行下新增拜访",
)
def create_trip_visit(
    trip_id: UUID,
    payload: VisitCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    """新增拜访会递增 Trip 版本号：既有计划立刻失效，避免"基于旧输入的计划"被当成当前有效。"""
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=VISITS_ENDPOINT,
        key=idempotency_key,
        payload={"trip_id": str(trip_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    visit = visit_service.create_visit(session, context.organization_id, trip_id, payload)
    body = VisitRead.model_validate(visit)
    return guard.finish(status_code=status.HTTP_201_CREATED, body=body)

def _trip_transition(
    session: Session,
    trip_id: UUID,
    context: ContextDep,
    idempotency_key: str | None,
    if_match: int | None,
    *,
    endpoint: str,
    action,  # noqa: ANN001 —— 服务层函数
):
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=endpoint,
        key=idempotency_key,
        payload={"trip_id": str(trip_id)},
    )
    if (replay := guard.begin()) is not None:
        return replay
    trip = action(
        session, context.organization_id, trip_id, expected_version=if_match
    )
    return guard.finish(status_code=status.HTTP_200_OK, body=trip_read(session, trip))


@router.post("/trips/{trip_id}/hold", response_model=TripRead, summary="暂停出行")
def hold_trip(
    trip_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    if_match: IfMatchDep,
):
    require_role(context, ROLE_TRAVELER)
    return _trip_transition(
        session,
        trip_id,
        context,
        idempotency_key,
        if_match,
        endpoint=TRIP_HOLD_ENDPOINT,
        action=trip_service.hold_trip,
    )


@router.post("/trips/{trip_id}/resume", response_model=TripRead, summary="恢复出行")
def resume_trip(
    trip_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    if_match: IfMatchDep,
):
    require_role(context, ROLE_TRAVELER)
    return _trip_transition(
        session,
        trip_id,
        context,
        idempotency_key,
        if_match,
        endpoint=TRIP_RESUME_ENDPOINT,
        action=trip_service.resume_trip,
    )


@router.post("/trips/{trip_id}/complete", response_model=TripRead, summary="完成出行")
def complete_trip(
    trip_id: UUID,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
    if_match: IfMatchDep,
):
    require_role(context, ROLE_TRAVELER)
    return _trip_transition(
        session,
        trip_id,
        context,
        idempotency_key,
        if_match,
        endpoint=TRIP_COMPLETE_ENDPOINT,
        action=trip_service.complete_trip,
    )




@router.get(
    "/trips/{trip_id}/closeout-readiness",
    response_model=TripCloseoutReadinessRead,
    summary="评估出差是否具备报销与财务结账条件（只读）",
)
def get_trip_closeout_readiness(
    trip_id: UUID,
    context: ContextDep,
    session: SessionDep,
):
    require_role(context, ROLE_VIEWER)
    return trip_closeout_service.build_trip_closeout_readiness(
        session, context.organization_id, trip_id
    )


@router.get(
    "/trips/{trip_id}/outcome",
    response_model=TripOutcomeRead,
    summary="出差结果分析与客户机会复盘（PRD P1）",
)
def get_trip_outcome(trip_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    return trip_outcome_service.build_outcome(session, context.organization_id, trip_id)
