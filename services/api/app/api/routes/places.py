"""地点接口。

``POST /places`` 采用**去重复用**语义（同名 + 同城 + 同国即同一地点，见
:func:`app.services.place_service.resolve_or_create_place`）：地图上多一个重复钉，
规划引擎就多算一段路程，而去重比事后合并便宜得多。因此本接口自身就是幂等的——
同一份输入重复提交只会得到同一个地点，不会产生第二条记录。

坐标来源必须可追溯：显式坐标标 ``client``，离线城市索引标 ``offline-city-index``，
查不到就明确 ``MISSING``，**绝不推测**（推测出来的坐标会让路线看起来可行但实际跑不通）。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.api.deps import ROLE_TRAVELER, ROLE_VIEWER, ContextDep, IdempotencyKeyDep, SessionDep, require_role
from app.api.errors import NotFound
from app.api.idempotent import IdempotentWrite
from app.api.pagination import clamp_limit, decode_cursor
from app.api.pagination import next_cursor as page_cursor
from app.api.schemas import Page, PlaceCreate, PlaceRead
from app.services import place_service

router = APIRouter(tags=["places"])

PLACES_ENDPOINT = "POST /v1/places"


@router.get("/places", response_model=Page[PlaceRead], summary="地点列表")
def list_places(
    context: ContextDep,
    session: SessionDep,
    query: str | None = Query(default=None, description="按名称或城市模糊检索"),
    country_code: str | None = Query(default=None, description="ISO 3166-1 alpha-2，如 CN/DE"),
    limit: int | None = Query(default=None, ge=1),
    cursor: str | None = Query(default=None),
):
    require_role(context, ROLE_VIEWER)
    page_size = clamp_limit(limit)
    offset = decode_cursor(cursor)
    places = place_service.list_places(
        session,
        context.organization_id,
        query=query,
        country_code=country_code,
        limit=page_size,
        offset=offset,
    )
    items = [PlaceRead.model_validate(place) for place in places]
    return Page[PlaceRead](items=items, next_cursor=page_cursor(places, offset, page_size))


@router.post(
    "/places",
    response_model=PlaceRead,
    status_code=status.HTTP_201_CREATED,
    summary="创建地点（同名同城同国自动复用）",
)
def create_place(
    payload: PlaceCreate,
    context: ContextDep,
    session: SessionDep,
    idempotency_key: IdempotencyKeyDep,
):
    require_role(context, ROLE_TRAVELER)
    guard = IdempotentWrite(
        session,
        context.organization_id,
        endpoint=PLACES_ENDPOINT,
        key=idempotency_key,
        payload=payload.model_dump(mode="json"),
    )
    if (replay := guard.begin()) is not None:
        return replay
    place, _reused = place_service.resolve_or_create_place(
        session,
        context.organization_id,
        name=payload.name,
        city=payload.city,
        country_code=payload.country_code,
        address_line=payload.address_line,
        region=payload.region,
        timezone=payload.timezone,
        lat=payload.lat,
        lon=payload.lon,
    )
    body = PlaceRead.model_validate(place)
    return guard.finish(status_code=status.HTTP_201_CREATED, body=body)


@router.get("/places/{place_id}", response_model=PlaceRead, summary="地点详情")
def get_place(place_id: UUID, context: ContextDep, session: SessionDep):
    require_role(context, ROLE_VIEWER)
    place = place_service.get_place(session, context.organization_id, place_id)
    if place is None:
        raise NotFound("地点不存在", details={"place_id": str(place_id)})
    return PlaceRead.model_validate(place)