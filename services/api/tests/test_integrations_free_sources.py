"""免钥公开源集成（ADR-0034）：Open-Meteo 天气 / OpenSky 航班 / OSRM 路由。

- 解析纯函数：响应结构缺失/类型不对按"不可用"抛出，不猜字段；
- 拉取服务：只有恶劣天气才落 WEATHER_DISRUPTION 信号；航班按 (trip, callsign) 挂信号；
  源未配置显式失败，绝不伪造；
- OSRM：GROUND 段取真实路网分钟，非地面段回退大圆估算（来源标注诚实）；
- 密钥文件（KMS 免费替代）：文件优先于环境变量。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.geo import Place
from app.models.integrations import ExternalSignal
from app.models.trip import Trip, Visit
from app.services import signal_pull_service
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import delete, text

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(org: str = ORG_A, user: str = USER_A) -> dict[str, str]:
    return {"X-Org-Id": org, "X-User-Id": user}


def _trip_with_visit(client: TestClient) -> tuple[str, str]:
    place = client.post(
        "/v1/places",
        headers=headers(),
        json={
            "name": "柏林客户总部",
            "lat": 52.52,
            "lon": 13.405,
            "city": "Berlin",
            "country_code": "DE",
        },
    )
    assert place.status_code == 201, place.text
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": "天气信号测试出行",
            "purpose": "客户拜访",
            "countries": ["DE"],
            "window_start": "2026-11-13T09:00:00+08:00",
            "window_end": "2026-11-15T18:00:00+08:00",
        },
    )
    assert trip.status_code == 201, trip.text
    visit = client.post(
        f"/v1/trips/{trip.json()['id']}/visits",
        headers=headers(),
        json={"place_id": place.json()["id"], "title": "柏林拜访", "service_minutes": 60},
    )
    assert visit.status_code == 201, visit.text
    return trip.json()["id"], place.json()["id"]


def _cleanup(trip_id: str, place_id: str) -> None:
    with org_session(UUID(ORG_A)) as session:
        trip_uuid = UUID(trip_id)
        place_uuid = UUID(place_id)
        session.execute(
            delete(ExternalSignal).where(
                ExternalSignal.organization_id == UUID(ORG_A),
                ExternalSignal.subject_ref == trip_id,
            )
        )
        # Visit.place_id is RESTRICT; delete visit rows before deleting their Place.
        session.execute(
            delete(Visit).where(
                Visit.organization_id == UUID(ORG_A),
                Visit.trip_id == trip_uuid,
            )
        )
        session.execute(
            delete(Trip).where(
                Trip.organization_id == UUID(ORG_A),
                Trip.id == trip_uuid,
            )
        )
        session.execute(
            delete(Place).where(
                Place.organization_id == UUID(ORG_A),
                Place.id == place_uuid,
            )
        )


# ---------------------------------------------------------------------------
# Open-Meteo 解析与阈值
# ---------------------------------------------------------------------------

OPEN_METEO_OK = {
    "latitude": 52.52,
    "longitude": 13.405,
    "current": {
        "time": "2026-11-12T10:00",
        "temperature_2m": 8.4,
        "wind_speed_10m": 61.2,
        "precipitation": 0.0,
        "weather_code": 3,
    },
}


def test_parse_current_ok():
    from app.integrations.weather import parse_current

    observation = parse_current(OPEN_METEO_OK)
    assert observation.wind_kmh == 61.2
    assert observation.observed_at == datetime(2026, 11, 12, 10, 0, tzinfo=UTC)


def test_parse_current_missing_block_raises():
    from app.integrations.weather import WeatherUnavailable, parse_current

    with pytest.raises(WeatherUnavailable):
        parse_current({"latitude": 1.0, "longitude": 1.0})


def test_severe_thresholds():
    from app.integrations.weather import WeatherObservation, is_severe

    calm = WeatherObservation(52.0, 13.0, 8.0, 12.0, 0.5, 1, None)
    windy = WeatherObservation(52.0, 13.0, 8.0, 50.0, 0.0, 3, None)
    rainy = WeatherObservation(52.0, 13.0, 8.0, 5.0, 10.0, 65, None)
    assert is_severe(calm) is False
    assert is_severe(windy) is True
    assert is_severe(rainy) is True


# ---------------------------------------------------------------------------
# OpenSky 解析
# ---------------------------------------------------------------------------

OPEN_SKY_OK = {
    "time": 1762940000,
    "states": [
        [
            "4b1816", "DLH441  ", "Germany", None, 1762939999,
            13.7322, 52.3576, 11887.2, False, 241.5, 91.75, None,
            None, 12557.56, "1000", False,
        ]
    ],
}


def test_parse_states_ok():
    from app.integrations.flights import parse_states

    states = parse_states(OPEN_SKY_OK)
    assert len(states) == 1
    state = states[0]
    assert state.icao24 == "4b1816"
    assert state.callsign == "DLH441"
    assert state.on_ground is False
    assert state.velocity_ms == 241.5


def test_parse_states_missing_array_raises():
    from app.integrations.flights import FlightDataUnavailable, parse_states

    with pytest.raises(FlightDataUnavailable):
        parse_states({"time": 1})


# ---------------------------------------------------------------------------
# OSRM 解析与 provider 工厂
# ---------------------------------------------------------------------------

OSRM_TABLE_OK = {
    "code": "Ok",
    "durations": [[0.0, 3600.0, 1800.0], [3600.0, 0.0, 900.0], [1800.0, 900.0, 0.0]],
    "distances": [[0.0, 80000.0, 30000.0], [80000.0, 0.0, 15000.0], [30000.0, 15000.0, 0.0]],
}


def test_osrm_parse_table():
    from app.integrations.roads import parse_table

    durations, distances = parse_table(OSRM_TABLE_OK)
    assert durations[0][1] == 3600.0
    assert distances[2][1] == 15000.0


def test_travel_provider_factory_osrm(monkeypatch):
    from app.config import get_settings
    from app.integrations.roads import OsrmRoadProvider
    from app.integrations.travel import get_travel_provider

    settings = get_settings()
    monkeypatch.setattr(settings, "travel_provider", "osrm", raising=False)
    assert isinstance(get_travel_provider(), OsrmRoadProvider)


# ---------------------------------------------------------------------------
# 拉取服务：恶劣天气才落信号；未配置源诚实失败
# ---------------------------------------------------------------------------


class StubWeatherProvider:
    name = "stub"

    def __init__(self, wind: float) -> None:
        self.wind = wind

    def fetch_current(self, lat: float, lon: float):
        from app.integrations.weather import WeatherObservation

        return WeatherObservation(
            lat, lon, 8.0, self.wind, 0.0, 3, datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
        )


def test_pull_weather_ingests_only_severe(client: TestClient):
    trip_id, place_id = _trip_with_visit(client)
    try:
        with org_session(UUID(ORG_A)) as session:
            calm = signal_pull_service.pull_weather(
                session, ORG_A, trip_id=UUID(trip_id),
                provider=StubWeatherProvider(wind=12.0),
            )
            assert calm["signals_ingested"] == 0
            assert calm["items"][0]["severe"] is False

            stormy = signal_pull_service.pull_weather(
                session, ORG_A, trip_id=UUID(trip_id),
                provider=StubWeatherProvider(wind=61.2),
            )
            assert stormy["signals_ingested"] == 1

            signal = session.query(ExternalSignal).filter_by(
                organization_id=ORG_A, provider="stub"
            ).one()
            assert signal.signal_type == "WEATHER_DISRUPTION"
            assert signal.payload["place_id"] == place_id
            assert signal.dedupe_key.startswith("stub:") or "place" in signal.dedupe_key
    finally:
        _cleanup(trip_id, place_id)


def test_pull_flight_unconfigured_raises_honestly(client: TestClient, monkeypatch):
    from app.config import get_settings
    from app.integrations.flights import FlightDataUnavailable

    settings = get_settings()
    monkeypatch.setattr(settings, "flight_provider", "unavailable", raising=False)
    trip_id, place_id = _trip_with_visit(client)
    try:
        with org_session(UUID(ORG_A)) as session:
            with pytest.raises(FlightDataUnavailable):
                signal_pull_service.pull_flight(
                    session, ORG_A, trip_id=UUID(trip_id), callsign="DLH441"
                )
    finally:
        _cleanup(trip_id, place_id)


def test_pull_flight_ingests_signal(client: TestClient):
    from datetime import datetime as dt

    class StubFlightProvider:
        name = "stub-sky"

        def fetch_by_callsign(self, callsign: str):
            from app.integrations.flights import FlightState

            return [
                FlightState(
                    icao24="4b1816",
                    callsign=callsign.strip().upper(),
                    origin_country="Germany",
                    latitude=52.35,
                    longitude=13.73,
                    baro_altitude_m=11887.2,
                    velocity_ms=241.5,
                    on_ground=False,
                    last_contact=dt(2026, 11, 12, 9, 0, tzinfo=UTC),
                )
            ]

    trip_id, place_id = _trip_with_visit(client)
    trip_two_id, place_two_id = _trip_with_visit(client)
    try:
        with org_session(UUID(ORG_A)) as session:
            first = signal_pull_service.pull_flight(
                session, ORG_A, trip_id=UUID(trip_id),
                callsign="DLH441", provider=StubFlightProvider(),
            )
            second = signal_pull_service.pull_flight(
                session, ORG_A, trip_id=UUID(trip_two_id),
                callsign="DLH441", provider=StubFlightProvider(),
            )
            assert first["signals_ingested"] == second["signals_ingested"] == 1
            signals = session.query(ExternalSignal).filter_by(
                organization_id=ORG_A, provider="stub-sky"
            ).order_by(ExternalSignal.subject_ref).all()
            assert len(signals) == 2
            assert {signal.subject_ref for signal in signals} == {trip_id, trip_two_id}
            assert all(signal.signal_type == "FLIGHT_STATUS" for signal in signals)
            assert all(signal.payload["icao24"] == "4b1816" for signal in signals)
            assert signals[0].dedupe_key != signals[1].dedupe_key
    finally:
        _cleanup(trip_id, place_id)
        _cleanup(trip_two_id, place_two_id)


# ---------------------------------------------------------------------------
# 路由：ADMIN 专属 + 未配置诚实失败
# ---------------------------------------------------------------------------


def test_weather_pull_route_requires_admin(client: TestClient):
    traveler = {
        "X-Org-Id": "33333333-3333-3333-3333-333333333333",
        "X-User-Id": "44444444-4444-4444-4444-444444444444",
    }
    response = client.post(
        "/v1/integrations/signals:pull-weather",
        headers=traveler,
        json={"trip_id": str(uuid.uuid4())},
    )
    assert response.status_code == 403, response.text


def test_flight_pull_route_honest_when_unconfigured(client: TestClient):
    from app.config import get_settings

    settings = get_settings()
    assert settings.flight_provider != "opensky"  # 本地默认未配置
    trip_id, place_id = _trip_with_visit(client)
    try:
        response = client.post(
            "/v1/integrations/signals:pull-flight",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={"trip_id": trip_id, "callsign": "DLH441"},
        )
        assert response.status_code == 503, response.text
        assert "未配置" in response.text
    finally:
        _cleanup(trip_id, place_id)


# ---------------------------------------------------------------------------
# 密钥文件（KMS 免费替代）
# ---------------------------------------------------------------------------


def test_field_encryption_reads_key_file(tmp_path: Path, monkeypatch):
    from app.config import get_settings
    from app.models.encrypted import EncryptedText

    key = Fernet.generate_key().decode()
    key_file = tmp_path / "field_key"
    key_file.write_text(key + "\n", encoding="utf-8")
    settings = get_settings()
    monkeypatch.setattr(settings, "field_encryption_key", None, raising=False)
    monkeypatch.setattr(settings, "field_encryption_key_file", str(key_file), raising=False)

    column = EncryptedText()
    token = column.process_bind_param("机票密文材料", None)
    assert token.startswith("gAAAAA")
    assert column.process_result_value(token, None) == "机票密文材料"


def test_field_encryption_file_overrides_env(tmp_path: Path, monkeypatch):
    from app.config import get_settings
    from app.models.encrypted import EncryptedText

    env_key = Fernet.generate_key().decode()
    file_key = Fernet.generate_key().decode()
    key_file = tmp_path / "field_key"
    key_file.write_text(file_key, encoding="utf-8")
    settings = get_settings()
    monkeypatch.setattr(settings, "field_encryption_key", env_key, raising=False)
    monkeypatch.setattr(settings, "field_encryption_key_file", str(key_file), raising=False)

    column = EncryptedText()
    token = column.process_bind_param("文件密钥加密", None)
    fernet_file = Fernet(file_key.encode())
    assert fernet_file.decrypt(token.encode()).decode() == "文件密钥加密"
