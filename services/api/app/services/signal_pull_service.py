"""免钥公开源的信号拉取（ADR-0034）。

把 Open-Meteo（天气）与 OpenSky（航班）的公开数据接进既有 external_signals 体系
（ADR-0005"可信信号接入"——只存事实，是否提醒由主动式运行时决定）：

- **天气**：按出行逐拜访地点拉当前天气；达到恶劣阈值（风 ≥50 km/h 或降水
  ≥10 mm）才落 ``WEATHER_DISRUPTION`` 信号，按 (place, 行情日) 幂等。
- **航班**：按 callsign 查 OpenSky 航迹，每架飞机落一条 ``FLIGHT_STATUS``，
  同机幂等更新（最新状态覆盖旧行）。

未配置的源显式失败（``*_Unavailable``），绝不伪造信号。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import NotFound, Unprocessable
from app.integrations.flights import (
    FlightDataUnavailable,
    FlightState,
    get_flight_provider,
)
from app.integrations.weather import (
    WeatherObservation,
    WeatherUnavailable,
    get_weather_provider,
    is_severe,
)
from app.models.flights import TripFlightSegment
from app.models.geo import Place
from app.models.trip import Trip, Visit
from app.services import external_service, flight_segment_service

_SIGNAL_VALIDITY = timedelta(hours=24)


def _trip_visits(session: Session, organization_id: UUID | str, trip_id: UUID) -> list[tuple]:
    trip = session.get(Trip, str(trip_id))
    if trip is None or str(trip.organization_id) != str(organization_id):
        raise NotFound("出行不存在")
    rows = session.execute(
        select(Visit.id, Place.id, Place.name, Place.lat, Place.lon)
        .join(Place, Visit.place_id == Place.id)
        .where(Visit.trip_id == trip.id)
        .order_by(Visit.time_window_start)
    ).all()
    return [(visit_id, str(place_id), name, lat, lon) for visit_id, place_id, name, lat, lon in rows]


def pull_weather(
    session: Session,
    organization_id: UUID | str,
    *,
    trip_id: UUID,
    provider=None,
) -> dict[str, Any]:
    """拉取出行目的地的当前天气；恶劣天气落 WEATHER_DISRUPTION 信号。"""
    if provider is None:
        provider = get_weather_provider()
    rows = _trip_visits(session, organization_id, trip_id)
    items: list[dict[str, Any]] = []
    ingested = 0
    for _, place_id, name, lat, lon in rows:
        if lat is None or lon is None:
            items.append(
                {"place_id": place_id, "place_name": name, "error": "地点缺坐标", "signal": False}
            )
            continue
        try:
            observation = provider.fetch_current(lat, lon)
        except WeatherUnavailable as exc:
            items.append({"place_id": place_id, "place_name": name, "error": str(exc), "signal": False})
            continue
        signal_ingested = False
        if is_severe(observation):
            observed_at = observation.observed_at or datetime.now(UTC)
            external_service.ingest(
                session,
                organization_id,
                payload=_weather_signal(
                    provider_name=provider.name,
                    trip_id=trip_id,
                    place_id=place_id,
                    place_name=name,
                    observation=observation,
                    observed_at=observed_at,
                ),
            )
            signal_ingested = True
            ingested += 1
        items.append(
            {
                "place_id": place_id,
                "place_name": name,
                "temperature_c": observation.temperature_c,
                "wind_kmh": observation.wind_kmh,
                "precipitation_mm": observation.precipitation_mm,
                "weather_code": observation.weather_code,
                "severe": True if signal_ingested else False,
                "signal": signal_ingested,
                "error": None,
            }
        )
    return {"trip_id": str(trip_id), "checked": len(items), "signals_ingested": ingested, "items": items}


def _weather_signal(
    *,
    provider_name: str,
    trip_id: UUID,
    place_id: str,
    place_name: str,
    observation: WeatherObservation,
    observed_at: datetime,
) -> Any:
    from app.api.schemas import ExternalSignalCreate

    return ExternalSignalCreate(
        provider=provider_name,
        signal_type="WEATHER_DISRUPTION",
        subject_type="TRIP",
        subject_ref=str(trip_id),
        payload={
            "place_id": place_id,
            "place_name": place_name,
            "temperature_c": observation.temperature_c,
            "wind_kmh": observation.wind_kmh,
            "precipitation_mm": observation.precipitation_mm,
            "weather_code": observation.weather_code,
            "latitude": observation.latitude,
            "longitude": observation.longitude,
        },
        confidence=0.9,
        observed_at=observed_at,
        valid_until=observed_at + _SIGNAL_VALIDITY,
        dedupe_key=f"{provider_name}:{place_id}:{observed_at.date().isoformat()}",
    )


def _normalise_tracking_reference(provider_name: str, value: str) -> str:
    """OpenSky callsigns are uppercase; other providers retain exact reference semantics."""
    cleaned = value.strip()
    return cleaned.upper() if provider_name.strip().casefold() == "opensky" else cleaned


def pull_flight(
    session: Session,
    organization_id: UUID | str,
    *,
    trip_id: UUID,
    callsign: str | None = None,
    flight_segment_id: UUID | None = None,
    provider=None,
) -> dict[str, Any]:
    """拉取航迹；若关联机票段，必须显式给出 segment id 且 provider/reference 完全匹配。

    OpenSky telemetry 只代表航空器状态快照。绑定不会从 callsign 自动推断，也不代表已确认延误。
    """
    _trip_visits(session, organization_id, trip_id)
    if provider is None:
        provider = get_flight_provider()
    provider_name = str(provider.name).strip()
    if not provider_name:
        raise Unprocessable("航班 Provider 缺少稳定名称")

    normalized_callsign = (
        _normalise_tracking_reference(provider_name, callsign)
        if callsign is not None
        else None
    )
    segment: TripFlightSegment | None = None
    if flight_segment_id is not None:
        segment = flight_segment_service.require_flight_segment(
            session,
            organization_id,
            trip_id,
            flight_segment_id,
            for_update=True,
        )
        if segment.booking_status == "CANCELLED":
            raise Unprocessable("已取消的机票段不能接收航迹状态")
        if not segment.tracking_provider or not segment.tracking_reference:
            raise Unprocessable("该机票段尚未配置 tracking provider/reference")
        if segment.tracking_provider.casefold() != provider_name.casefold():
            raise Unprocessable(
                "航班 Provider 与机票段 tracking_provider 不匹配",
                details={
                    "expected_provider": segment.tracking_provider,
                    "received_provider": provider_name,
                    "flight_segment_id": str(segment.id),
                },
            )
        expected_reference = _normalise_tracking_reference(
            provider_name, segment.tracking_reference
        )
        if normalized_callsign is not None and normalized_callsign != expected_reference:
            raise Unprocessable(
                "callsign 与机票段 tracking_reference 不匹配",
                details={
                    "flight_segment_id": str(segment.id),
                    "tracking_reference": segment.tracking_reference,
                },
            )
        normalized_callsign = expected_reference
    if not normalized_callsign:
        raise Unprocessable("按机票段拉取航迹需要 tracking_reference；普通拉取需要 callsign")

    try:
        states: list[FlightState] = provider.fetch_by_callsign(normalized_callsign)
    except FlightDataUnavailable:
        raise

    ingested = 0
    items: list[dict[str, Any]] = []
    for state in states[:10]:  # 配额友好：同 callsign 最多取 10 架
        state_callsign = _normalise_tracking_reference(provider_name, state.callsign)
        if state_callsign != normalized_callsign:
            raise FlightDataUnavailable(
                "航班 Provider 返回的 callsign 与请求不一致；本次状态未写入"
            )
        now = datetime.now(UTC)
        external_service.ingest(
            session,
            organization_id,
            payload=_flight_signal(
                provider_name=provider_name,
                trip_id=trip_id,
                callsign=normalized_callsign,
                state=state,
                observed_at=now,
                flight_segment_id=segment.id if segment is not None else None,
            ),
        )
        ingested += 1
        items.append(
            {
                "icao24": state.icao24,
                "callsign": state.callsign,
                "origin_country": state.origin_country,
                "latitude": state.latitude,
                "longitude": state.longitude,
                "altitude_m": state.baro_altitude_m,
                "velocity_ms": state.velocity_ms,
                "on_ground": state.on_ground,
                "last_contact": state.last_contact.isoformat(),
            }
        )
    return {
        "trip_id": str(trip_id),
        "callsign": normalized_callsign,
        "flight_segment_id": str(segment.id) if segment is not None else None,
        "aircraft": len(items),
        "signals_ingested": ingested,
        "items": items,
    }


def _flight_signal(
    *,
    provider_name: str,
    trip_id: UUID,
    callsign: str,
    state: FlightState,
    observed_at: datetime,
    flight_segment_id: UUID | None = None,
) -> Any:
    from app.api.schemas import ExternalSignalCreate

    scope = str(flight_segment_id) if flight_segment_id is not None else callsign
    payload = {
        "icao24": state.icao24,
        "callsign": state.callsign,
        "origin_country": state.origin_country,
        "latitude": state.latitude,
        "longitude": state.longitude,
        "baro_altitude_m": state.baro_altitude_m,
        "velocity_ms": state.velocity_ms,
        "on_ground": state.on_ground,
        "last_contact": state.last_contact.isoformat(),
    }
    if flight_segment_id is not None:
        payload["flight_segment_id"] = str(flight_segment_id)
    return ExternalSignalCreate(
        provider=provider_name,
        signal_type="FLIGHT_STATUS",
        subject_type="TRIP",
        subject_ref=str(trip_id),
        payload=payload,
        confidence=0.95,
        observed_at=observed_at,
        valid_until=observed_at + _SIGNAL_VALIDITY,
        dedupe_key=f"{provider_name}:{trip_id}:{scope}:{state.icao24.strip().lower()}",
    )


__all__ = ["pull_flight", "pull_weather"]
