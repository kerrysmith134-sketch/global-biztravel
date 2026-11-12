"""当地生活与文化指南（PRD 方向五：Local Intelligence Agent，离线目录版）。

按一次出行的国家与拜访城市组装"当天可用"的指南条目——礼仪、用餐、支付、交通、网络、
治安、常用表达，以及城市级贴士。要点：

- **离线目录**：内容来自仓库内的 `data/local_guide_catalog.json`，与签证目录同一套路——
  不联网、可版本化、可评审；实时源（餐厅推荐、POI、天气）未接入时用 `data_gaps` 显式标注，
  不给编造的"推荐"。
- **与行程结合**：城市贴士按出行拜访地点的 `city` 匹配，只返回用户实际会去的城市。
- 只读：指南不写库、不触发任务，任何角色可看（路由层仍按组织隔离取行程）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.geo import Place
from app.models.trip import Trip, Visit
from app.services import trip_service

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "local_guide_catalog.json"

GAPS = (
    {
        "code": "LIVE_POI_NOT_CONNECTED",
        "message": "实时餐厅/景点/POI 推荐源未接入，本指南只提供固定的商务与生活要点。",
    },
    {
        "code": "WEATHER_NOT_CONNECTED",
        "message": "天气与中断信息需接入公开数据源后才会出现在指南里。",
    },
)


@lru_cache
def load_catalog() -> dict:
    with CATALOG_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)


def _visited_cities(session: Session, organization_id: str, trip_id: UUID) -> list[str]:
    rows = session.execute(
        select(Place.city)
        .join(Visit, Visit.place_id == Place.id)
        .where(
            Visit.organization_id == organization_id,
            Visit.trip_id == trip_id,
            Place.city.isnot(None),
        )
        .distinct()
    ).all()
    return sorted({str(row[0]) for row in rows if row[0]})


def build_guide(
    session: Session, organization_id: UUID | str, trip_id: UUID | str
) -> dict:
    trip: Trip = trip_service.require_trip(session, organization_id, trip_id)
    catalog = load_catalog().get("countries", {})
    cities = _visited_cities(session, str(organization_id), UUID(str(trip_id)))

    countries: list[dict] = []
    missing: list[dict] = []
    for code in trip.countries or []:
        entry = catalog.get(code.upper())
        if entry is None:
            missing.append(
                {
                    "code": "COUNTRY_GUIDE_MISSING",
                    "message": f"目录暂无 {code.upper()} 的当地指南条目。",
                    "country_code": code.upper(),
                }
            )
            continue
        city_tips = [
            {"city": city, "tips": tips["tips"]}
            for city, tips in sorted(entry.get("cities", {}).items())
            if city in cities
        ]
        countries.append(
            {
                "country_code": code.upper(),
                "name": entry["name"],
                "sections": entry.get("sections", []),
                "cities": city_tips,
            }
        )

    gaps = [dict(gap) for gap in GAPS] + missing
    weather = _current_weather(session, str(organization_id), trip_id)
    if weather:
        # 实时天气接入成功（ADR-0034）：按城市附带当前观测，摘除既有 gap。
        gaps = [gap for gap in gaps if gap.get("code") != "WEATHER_NOT_CONNECTED"]
    return {
        "trip_id": str(trip.id),
        "countries": countries,
        "cities_covered": cities,
        "weather": weather,
        "data_gaps": gaps,
    }


#: 指南页最多带的天气观测数（请求时长预算内，超出城市不查询）。
_MAX_WEATHER_LOOKUPS = 8


def _current_weather(session: Session, organization_id: str, trip_id: UUID | str) -> list[dict]:
    """逐拜访地点取 Open-Meteo 当前天气；源未配置/失败时返回空列表（gap 保留）。"""
    from app.integrations.weather import WeatherUnavailable, get_weather_provider

    if get_settings().weather_provider != "open-meteo":
        return []
    rows = session.execute(
        select(Place.id, Place.name, Place.lat, Place.lon)
        .join(Visit, Visit.place_id == Place.id)
        .where(
            Visit.organization_id == organization_id,
            Visit.trip_id == trip_id,
            Place.lat.isnot(None),
            Place.lon.isnot(None),
        )
        .order_by(Visit.time_window_start)
        .limit(_MAX_WEATHER_LOOKUPS)
    ).all()
    provider = get_weather_provider()
    observations: list[dict] = []
    for place_id, name, lat, lon in rows:
        try:
            observation = provider.fetch_current(lat, lon)
        except WeatherUnavailable:
            return []  # 源不可用 ⇒ 整体回到 gap 状态，不输出半份天气
        observations.append(
            {
                "place_id": str(place_id),
                "place_name": name,
                "temperature_c": observation.temperature_c,
                "wind_kmh": observation.wind_kmh,
                "precipitation_mm": observation.precipitation_mm,
                "weather_code": observation.weather_code,
                "observed_at": (
                    observation.observed_at.isoformat() if observation.observed_at else None
                ),
            }
        )
    return observations


__all__ = ["build_guide", "load_catalog"]
