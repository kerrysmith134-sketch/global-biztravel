"""客户档案接口。

客户是"组织级"资源而不是"出行级"资源：同一个客户会在多次出行里被拜访，档案要连续。
因此导入确认时是**复用**（只补空缺字段）而不是覆盖——避免一次导入把人工维护的
联系方式、行业、分级冲掉（见 :func:`app.services.customer_service.resolve_or_create_customer`）。
"""

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
    CustomerCreate,
    CustomerMapCustomerRead,
    CustomerMapLocationsRead,
    CustomerMapLocationRead,
    CustomerMapPlaceRead,
    CustomerMapSummaryRead,
    CustomerPlaceCreate,
    CustomerPlaceRead,
    CustomerRead,
    Page,
)
from app.services import customer_map_service, customer_service, place_service

router = APIRouter(tags=["customers"])

CUSTOMERS_ENDPOINT = "POST /v1/customers"


@router.get("/customers", response_model=Page[CustomerRead], summary="客户列表")
def list_customers(
    context: ContextDep,
    session: SessionDep,
    query: str | None = Query(default=None, description="按名称或外部编号模糊检索"),
    tier: str | None = Query(default=None, description="按客户分级过滤：A/B/C"),
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None),
):
    require_role(context, ROLE_VIEWER)
    page_size = clamp_limit(limit)
    offset = decode_cursor(cursor)
    customers = customer_service.list_customers(
        session, context.organization_id, query=query, tier=tier, limit=page_size, offset=offset
    )
    items = [CustomerRead.model_validate(customer) for customer in customers]
    return Page[CustomerRead](items=items, next_cursor=page_cursor(customers, offset, page_size))


@router.post(
    "/customers",
    response_model=CustomerRead,
    status_code=status.HTTP_201_CREATED,
    summary="创建客户",
)
def create_customer(
    payload: CustomerCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    """``external_ref`` 已存在时返回 409 —— 客户档案不允许同编号两条。"""
    require_role(context, ROLE_ADMIN)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=CUSTOMERS_ENDPOINT,
        key=idempotency_key,
        payload=payload.model_dump(mode="json"),
    )
    # 先检查、后执行：重放请求不得再建第二份客户档案（external_ref 唯一，会直接 409 之外还多脏数据）。
    if (replay := guard.begin()) is not None:
        return replay
    customer = customer_service.create_customer(
        session, context.organization_id, payload, owner_user_id=context.user_id
    )
    body = CustomerRead.model_validate(customer)
    return guard.finish(status_code=status.HTTP_201_CREATED, body=body)


@router.get(
    "/customers/map-locations",
    response_model=CustomerMapLocationsRead,
    summary="组织级客户地图位置（单次聚合查询）",
)
def list_customer_map_locations(
    context: ContextDep,
    session: SessionDep,
    query: str | None = Query(default=None, max_length=200),
    tier: str | None = Query(default=None, pattern="^(A|B|C|a|b|c)$"),
    country_code: str | None = Query(default=None, min_length=2, max_length=2),
    geocode_status: str | None = Query(default=None, pattern="^(MISSING|RESOLVED|FAILED|missing|resolved|failed)$"),
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None),
):
    require_role(context, ROLE_VIEWER)
    page_size = clamp_limit(limit)
    offset = decode_cursor(cursor)
    result = customer_map_service.list_customer_map_locations(
        session,
        context.organization_id,
        query=query,
        tier=tier,
        country_code=country_code,
        geocode_status=geocode_status,
        limit=page_size,
        offset=offset,
    )
    items = [
        CustomerMapLocationRead(
            customer=CustomerMapCustomerRead(
                id=customer.id,
                name=customer.name,
                tier=customer.tier,
                country_code=customer.country_code,
                industry=customer.industry,
                status=customer.status,
            ),
            place=CustomerMapPlaceRead(
                id=place.id,
                label=link.label,
                is_primary=link.is_primary,
                name=place.name,
                address_line=place.address_line,
                city=place.city,
                region=place.region,
                country_code=place.country_code,
                timezone=place.timezone,
                lat=place.lat,
                lon=place.lon,
                geocode_status=place.geocode_status,
                geocode_source=place.geocode_source,
                geocode_confidence=place.geocode_confidence,
            ),
        )
        for customer, link, place in result["rows"]
    ]
    return CustomerMapLocationsRead(
        items=items,
        summary=CustomerMapSummaryRead(**result["summary"]),
        next_cursor=page_cursor(items, offset, page_size),
    )


@router.get("/customers/{customer_id}", response_model=CustomerRead, summary="客户详情")
def get_customer(customer_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    customer = customer_service.get_customer(session, context.organization_id, customer_id)
    if customer is None:
        raise NotFound("客户不存在", details={"customer_id": str(customer_id)})
    return CustomerRead.model_validate(customer)

def _require_customer(session, organization_id, customer_id: UUID):
    """客户不存在或不属于当前租户 → 404（跨租户不泄漏存在性）。"""
    customer = customer_service.get_customer(session, organization_id, customer_id)
    if customer is None:
        raise NotFound("客户不存在", details={"customer_id": str(customer_id)})
    return customer


@router.get(
    "/customers/{customer_id}/places",
    response_model=list[CustomerPlaceRead],
    summary="客户的全部地址（一家企业多个地址，PRD P0-3）",
)
def list_customer_places(customer_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    _require_customer(session, context.organization_id, customer_id)
    return [
        CustomerPlaceRead(
            place_id=place.id,
            label=link.label,
            is_primary=link.is_primary,
            name=place.name,
            address_line=place.address_line,
            city=place.city,
            region=place.region,
            country_code=place.country_code,
            lat=place.lat,
            lon=place.lon,
            geocode_status=place.geocode_status,
            geocode_source=place.geocode_source,
            geocode_confidence=place.geocode_confidence,
        )
        for link, place in place_service.list_customer_places(
            session, context.organization_id, customer_id
        )
    ]


@router.post(
    "/customers/{customer_id}/places",
    response_model=CustomerPlaceRead,
    status_code=status.HTTP_201_CREATED,
    summary="为客户关联一个地址（幂等）",
)
def link_customer_place(
    customer_id: UUID,
    payload: CustomerPlaceCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=f"POST /v1/customers/{customer_id}/places",
        key=idempotency_key,
        payload={"customer_id": str(customer_id), **payload.model_dump(mode="json")},
    )
    if (replay := guard.begin()) is not None:
        return replay
    _require_customer(session, context.organization_id, customer_id)
    place = place_service.get_place(session, context.organization_id, payload.place_id)
    if place is None:
        raise NotFound("地点不存在", details={"place_id": str(payload.place_id)})
    link = place_service.link_customer_place(
        session,
        context.organization_id,
        customer_id=customer_id,
        place_id=payload.place_id,
        label=payload.label,
        is_primary=payload.is_primary,
    )
    body = CustomerPlaceRead(
        place_id=place.id,
        label=link.label,
        is_primary=link.is_primary,
        name=place.name,
        address_line=place.address_line,
        city=place.city,
        region=place.region,
        country_code=place.country_code,
        lat=place.lat,
        lon=place.lon,
        geocode_status=place.geocode_status,
        geocode_source=place.geocode_source,
        geocode_confidence=place.geocode_confidence,
    )
    return guard.finish(status_code=status.HTTP_201_CREATED, body=body)


@router.delete(
    "/customers/{customer_id}/places/{place_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="解除客户—地址关联（不删地点本身）",
)
def unlink_customer_place(
    customer_id: UUID, place_id: UUID, context: ContextDep, session: SessionDep
):
    require_role(context, ROLE_TRAVELER)
    _require_customer(session, context.organization_id, customer_id)
    removed = place_service.unlink_customer_place(
        session, context.organization_id, customer_id, place_id
    )
    if not removed:
        raise NotFound(
            "客户与地点未关联",
            details={"customer_id": str(customer_id), "place_id": str(place_id)},
        )
