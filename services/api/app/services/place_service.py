"""地点服务：客户拜访地址的创建、去重复用与坐标解析。

去重策略（第 4.4 章）：**同名同城同国即视为同一地点**。理由是同一场出行里"上海浦东
张江路 1 号"与"张江路1号"这类写法差异不应产生两个物理点，否则地图上会多出一个钉、
规划引擎会多算一段路程。跨城同名（如两个国家的"Springfield"）由 ``country_code`` 区分。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.schemas import PlaceCreate
from app.models.geo import CustomerPlace, Place
from app.services.geocoding import Geocoder, GeocodeResult, get_geocoder


def _normalize_name(value: str) -> str:
    return " ".join(value.split()).casefold()


def find_place(
    session: Session,
    organization_id: UUID | str,
    *,
    name: str,
    city: str | None,
    country_code: str | None,
) -> Place | None:
    stmt = select(Place).where(
        Place.organization_id == organization_id,
        func.lower(func.btrim(Place.name)) == _normalize_name(name),
    )
    if city:
        stmt = stmt.where(func.coalesce(Place.city, "") == city)
    if country_code:
        stmt = stmt.where(func.coalesce(Place.country_code, "") == country_code.upper())
    return session.scalars(stmt.order_by(Place.created_at).limit(1)).one_or_none()


def create_place(
    session: Session,
    organization_id: UUID | str,
    payload: PlaceCreate,
    *,
    geocoder: Geocoder | None = None,
) -> Place:
    """按输入建地点。坐标优先级：显式坐标 > 离线城市索引 > 缺失（MISSING）。"""
    place = Place(
        organization_id=organization_id,
        name=payload.name.strip(),
        address_line=payload.address_line,
        city=payload.city,
        region=payload.region,
        country_code=payload.country_code.upper() if payload.country_code else None,
        timezone=payload.timezone,
        lat=payload.lat,
        lon=payload.lon,
    )
    if payload.lat is not None and payload.lon is not None:
        place.geocode_status = "RESOLVED"
        place.geocode_source = "client"
        place.geocode_confidence = 1.0
    else:
        _apply_geocode(place, geocoder or get_geocoder())
    session.add(place)
    session.flush()
    return place


def resolve_or_create_place(
    session: Session,
    organization_id: UUID | str,
    *,
    name: str,
    city: str | None = None,
    country_code: str | None = None,
    address_line: str | None = None,
    region: str | None = None,
    timezone: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    is_immovable: bool = False,
    geocoder: Geocoder | None = None,
) -> tuple[Place, bool]:
    """返回 ``(place, reused)``。``reused=True`` 表示命中已有地点，未新建。"""
    existing = find_place(
        session, organization_id, name=name, city=city, country_code=country_code
    )
    if existing is not None:
        # 已有地点缺坐标，而这次带了坐标 → 补齐（导入常见：首行只有城市，后补了详细地址）。
        if existing.lat is None and lat is not None and lon is not None:
            existing.lat, existing.lon = lat, lon
            existing.geocode_status = "RESOLVED"
            existing.geocode_source = "spreadsheet"
            existing.geocode_confidence = 1.0
        elif existing.geocode_status == "MISSING":
            _apply_geocode(existing, geocoder or get_geocoder())
        return existing, True

    place = Place(
        organization_id=organization_id,
        name=name.strip(),
        address_line=address_line,
        city=city,
        region=region,
        country_code=country_code.upper() if country_code else None,
        timezone=timezone,
        lat=lat,
        lon=lon,
    )
    if lat is not None and lon is not None:
        place.geocode_status = "RESOLVED"
        place.geocode_source = "spreadsheet"
        place.geocode_confidence = 1.0
    else:
        _apply_geocode(place, geocoder or get_geocoder())
    session.add(place)
    session.flush()
    return place, False


def _apply_geocode(place: Place, geocoder: Geocoder) -> None:
    """用离线城市索引补坐标；查不到就明确标记 MISSING，不猜。"""
    result: GeocodeResult | None = geocoder.resolve(city=place.city, country_code=place.country_code)
    if result is None:
        place.geocode_status = "MISSING"
        place.geocode_source = None
        place.geocode_confidence = None
        return
    place.lat = result.lat
    place.lon = result.lon
    place.timezone = place.timezone or result.timezone
    place.country_code = place.country_code or result.country_code
    place.geocode_status = "RESOLVED"
    place.geocode_source = result.source
    place.geocode_confidence = result.confidence


def link_customer_place(
    session: Session,
    organization_id: UUID | str,
    *,
    customer_id: UUID,
    place_id: UUID,
    label: str | None = None,
    is_primary: bool = False,
) -> CustomerPlace:
    """建立客户—地点关联；已存在则原样返回（幂等）。"""
    existing = session.scalars(
        select(CustomerPlace).where(
            CustomerPlace.organization_id == organization_id,
            CustomerPlace.customer_id == customer_id,
            CustomerPlace.place_id == place_id,
        )
    ).one_or_none()
    if existing is not None:
        return existing
    link = CustomerPlace(
        organization_id=organization_id,
        customer_id=customer_id,
        place_id=place_id,
        label=label,
        is_primary=is_primary,
    )
    session.add(link)
    session.flush()
    return link


def get_place(session: Session, organization_id: UUID | str, place_id: UUID) -> Place | None:
    return session.scalars(
        select(Place).where(Place.organization_id == organization_id, Place.id == place_id)
    ).one_or_none()


def list_places(
    session: Session,
    organization_id: UUID | str,
    *,
    query: str | None = None,
    country_code: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Place]:
    stmt = select(Place).where(Place.organization_id == organization_id)
    if query:
        pattern = f"%{query.strip()}%"
        stmt = stmt.where(Place.name.ilike(pattern) | func.coalesce(Place.city, "").ilike(pattern))
    if country_code:
        stmt = stmt.where(Place.country_code == country_code.upper())
    stmt = stmt.order_by(Place.name).limit(limit).offset(offset)
    return list(session.scalars(stmt).all())