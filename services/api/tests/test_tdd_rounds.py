"""TDD 记分册（20+ 轮，ADR-0034 批次的零测试盲区 + 行为锁死）。

每轮 = 一条行为锁死测试。红阶段抓到的真实缺陷在提交说明里逐条记录；
与 44 轮 TDD（e233d15）同一纪律：先红后绿，红不在则记"锁死"轮。
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import UTC, datetime
from uuid import UUID

import pytest
from app.api.errors import NotFound
from app.db import org_session
from app.main import app
from app.models.integrations import ExternalSignal
from app.services import (
    receipt_ocr_service,
    signal_pull_service,
    visa_catalog_override_service,
)
from fastapi.testclient import TestClient
from sqlalchemy import text

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(org: str = ORG_A, user: str = USER_A) -> dict[str, str]:
    return {"X-Org-Id": org, "X-User-Id": user}


def _trip_with_visit(client: TestClient, name: str) -> tuple[str, str]:
    place = client.post(
        "/v1/places",
        headers=headers(),
        json={"name": f"{name}客户", "lat": 52.52, "lon": 13.405, "city": "Berlin"},
    )
    assert place.status_code == 201, place.text
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"{name}出行",
            "window_start": "2026-11-13T09:00:00+08:00",
            "window_end": "2026-11-15T18:00:00+08:00",
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    visit = client.post(
        f"/v1/trips/{trip.json()['id']}/visits",
        headers=headers(),
        json={"place_id": place.json()["id"], "title": f"{name}拜访", "service_minutes": 60},
    )
    assert visit.status_code == 201, visit.text
    return trip.json()["id"], place.json()["id"]


def _cleanup_trip(trip_id: str, place_id: str) -> None:
    with org_session(UUID(ORG_A)) as session:
        session.execute(
            text("DELETE FROM external_signals WHERE subject_ref = :r"), {"r": trip_id}
        )
        session.execute(text("DELETE FROM visits WHERE trip_id = :t"), {"t": trip_id})
        session.execute(text("DELETE FROM trips WHERE id = :t"), {"t": trip_id})
        session.execute(text("DELETE FROM places WHERE id = :p"), {"p": place_id})


class StubWeather:
    name = "stub"

    def __init__(self, wind: float, *, track: bool = False) -> None:
        self.wind = wind
        self.calls: list[tuple[float, float]] = []

    def fetch_current(self, lat: float, lon: float):
        from app.integrations.weather import WeatherObservation

        self.calls.append((lat, lon))
        return WeatherObservation(
            lat, lon, 8.0, self.wind, 0.0, 3, datetime(2026, 11, 12, 10, 0, tzinfo=UTC)
        )


class StubWeatherBroken(StubWeather):
    def fetch_current(self, lat: float, lon: float):
        from app.integrations.weather import WeatherUnavailable

        raise WeatherUnavailable("模拟源挂了")


class StubFlight:
    name = "stub-sky"

    def __init__(self, count: int) -> None:
        self.count = count

    def fetch_by_callsign(self, callsign: str):
        from app.integrations.flights import FlightState

        return [
            FlightState(
                icao24=f"hex{i:04d}",
                callsign=callsign.strip().upper(),
                origin_country="Germany",
                latitude=52.0 + i * 0.01,
                longitude=13.0,
                baro_altitude_m=11000.0,
                velocity_ms=220.0,
                on_ground=False,
                last_contact=datetime(2026, 11, 12, 9, 0, tzinfo=UTC),
            )
            for i in range(self.count)
        ]


# ---------------------------------------------------------------------------
# 轮 9–13：信号拉取的幂等与边界
# ---------------------------------------------------------------------------


def test_round_09_weather_repull_updates_same_signal(client: TestClient):
    """同日重拉：幂等更新同一行，不产生重复信号。"""
    trip_id, place_id = _trip_with_visit(client, "轮9")
    try:
        with org_session(UUID(ORG_A)) as session:
            first = signal_pull_service.pull_weather(
                session, ORG_A, trip_id=UUID(trip_id), provider=StubWeather(wind=61.2)
            )
            second = signal_pull_service.pull_weather(
                session, ORG_A, trip_id=UUID(trip_id), provider=StubWeather(wind=63.0)
            )
            assert first["signals_ingested"] == 1
            assert second["signals_ingested"] == 1
            count = session.query(ExternalSignal).filter_by(
                organization_id=ORG_A, provider="stub"
            ).count()
            assert count == 1  # 幂等：更新而非新增
    finally:
        _cleanup_trip(trip_id, place_id)


def test_round_10_weather_skips_place_without_coords(client: TestClient):
    """无坐标地点：进 error 列表且不发起任何请求。"""
    place = client.post(
        "/v1/places", headers=headers(), json={"name": "无坐标客户"}
    )
    assert place.status_code == 201
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": "轮10出行",
            "window_start": "2026-11-13T09:00:00+08:00",
            "window_end": "2026-11-15T18:00:00+08:00",
        },
    )
    assert trip.status_code == 201
    client.post(
        f"/v1/trips/{trip.json()['id']}/visits",
        headers=headers(),
        json={"place_id": place.json()["id"], "title": "拜访", "service_minutes": 60},
    )
    trip_id, place_id = trip.json()["id"], place.json()["id"]
    stub = StubWeather(wind=90.0)
    try:
        with org_session(UUID(ORG_A)) as session:
            summary = signal_pull_service.pull_weather(
                session, ORG_A, trip_id=UUID(trip_id), provider=stub
            )
            assert summary["checked"] == 1
            assert summary["items"][0]["error"] == "地点缺坐标"
            assert summary["signals_ingested"] == 0
            assert stub.calls == []  # 没有发起任何天气请求
    finally:
        _cleanup_trip(trip_id, place_id)


def test_round_11_parse_current_bad_timestamp_raises():
    """坏时间戳：按不可用抛出，不猜时间。"""
    from app.integrations.weather import WeatherUnavailable, parse_current

    payload = {
        "latitude": 1.0,
        "longitude": 1.0,
        "current": {"time": "not-a-date", "temperature_2m": 1.0, "wind_speed_10m": 1.0},
    }
    with pytest.raises(WeatherUnavailable):
        parse_current(payload)


def test_round_12_flight_caps_at_ten_aircraft(client: TestClient):
    """同 callsign 命中超过 10 架：截断到 10 条信号（外部源配额友好）。"""
    trip_id, place_id = _trip_with_visit(client, "轮12")
    try:
        with org_session(UUID(ORG_A)) as session:
            summary = signal_pull_service.pull_flight(
                session, ORG_A, trip_id=UUID(trip_id),
                callsign="TEST12", provider=StubFlight(count=25),
            )
            assert summary["aircraft"] == 10
            assert summary["signals_ingested"] == 10
    finally:
        _cleanup_trip(trip_id, place_id)


def test_round_13_flight_empty_states_ingests_nothing(client: TestClient):
    """空航迹：不落任何信号，诚实返回 0。"""
    trip_id, place_id = _trip_with_visit(client, "轮13")
    try:
        with org_session(UUID(ORG_A)) as session:
            summary = signal_pull_service.pull_flight(
                session, ORG_A, trip_id=UUID(trip_id),
                callsign="NOTHERE", provider=StubFlight(count=0),
            )
            assert summary["aircraft"] == 0
            assert summary["signals_ingested"] == 0
    finally:
        _cleanup_trip(trip_id, place_id)


# ---------------------------------------------------------------------------
# 轮 14–15：指南天气的两条路径
# ---------------------------------------------------------------------------


def test_round_14_guide_weather_failure_keeps_gap(client: TestClient, monkeypatch):
    """天气源挂掉：weather 为空、gap 保留——绝不输出半份天气。"""
    from app.config import get_settings
    from app.integrations import weather as weather_module
    from app.services import local_guide_service

    settings = get_settings()
    monkeypatch.setattr(settings, "weather_provider", "open-meteo", raising=False)
    monkeypatch.setattr(
        weather_module, "get_weather_provider", lambda: StubWeatherBroken(wind=0.0)
    )
    trip_id, place_id = _trip_with_visit(client, "轮14")
    try:
        with org_session(UUID(ORG_A)) as session:
            guide = local_guide_service.build_guide(session, ORG_A, trip_id)
            assert guide["weather"] == []
            assert any(g["code"] == "WEATHER_NOT_CONNECTED" for g in guide["data_gaps"])
    finally:
        _cleanup_trip(trip_id, place_id)


def test_round_15_guide_weather_success_removes_gap(client: TestClient, monkeypatch):
    """天气接入成功：weather 非空且 WEATHER_NOT_CONNECTED gap 被摘除。"""
    from app.config import get_settings
    from app.integrations import weather as weather_module
    from app.services import local_guide_service

    settings = get_settings()
    monkeypatch.setattr(settings, "weather_provider", "open-meteo", raising=False)
    monkeypatch.setattr(
        weather_module, "get_weather_provider", lambda: StubWeather(wind=12.0)
    )
    trip_id, place_id = _trip_with_visit(client, "轮15")
    try:
        with org_session(UUID(ORG_A)) as session:
            guide = local_guide_service.build_guide(session, ORG_A, trip_id)
            assert len(guide["weather"]) == 1
            assert guide["weather"][0]["place_name"] == "轮15客户"
            assert all(g["code"] != "WEATHER_NOT_CONNECTED" for g in guide["data_gaps"])
    finally:
        _cleanup_trip(trip_id, place_id)


# ---------------------------------------------------------------------------
# 轮 16–19：OSRM provider 的错误路径与语义
# ---------------------------------------------------------------------------


class _FakeOSRMResponse:
    def __init__(self, payload) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


def _osrm_stub(payload):

    return lambda **kwargs: _FakeOSRMResponse(payload)


def _make_stops_and_origin():

    from travel_planner import Origin, StopInput

    origin = Origin(place_id=uuid.uuid4(), lat=52.52, lon=13.405)
    stops = [
        StopInput(
            visit_id=uuid.uuid4(),
            place_id=uuid.uuid4(),
            lat=48.13,
            lon=11.58,
            service_minutes=60,
            movement_mode=mode,
        )
        for mode in ("GROUND", "AIR")
    ]
    return origin, stops


def test_round_16_osrm_error_code_raises():
    from app.integrations.roads import OsrmRoadProvider
    from app.integrations.travel import TravelProviderUnavailable

    origin, stops = _make_stops_and_origin()
    provider = OsrmRoadProvider(base_url="http://mock")
    import app.integrations.roads as roads

    original_get = roads.httpx.get
    roads.httpx.get = lambda *args, **kw: _FakeOSRMResponse({"code": "NoRoute"})
    try:
        with pytest.raises(TravelProviderUnavailable):
            provider.estimate(origin, stops)
    finally:
        roads.httpx.get = original_get


def test_round_17_osrm_missing_matrix_cell_raises():
    from app.integrations.roads import OsrmRoadProvider
    from app.integrations.travel import TravelProviderUnavailable

    origin, (ground, _) = _make_stops_and_origin()
    provider = OsrmRoadProvider(base_url="http://mock")
    payload = {
        "code": "Ok",
        "durations": [[0.0, None], [None, 0.0]],
        "distances": [[0.0, 80000.0], [80000.0, 0.0]],
    }
    import app.integrations.roads as roads

    original_get = roads.httpx.get
    roads.httpx.get = lambda *args, **kw: _FakeOSRMResponse(payload)
    try:
        with pytest.raises(TravelProviderUnavailable):
            provider.estimate(origin, [ground])
    finally:
        roads.httpx.get = original_get


def test_round_18_osrm_duration_ceil_semantics():
    """3600.0s → 60 分钟；3601s → 61 分钟（向上取整，保守）。"""
    from app.integrations.roads import OsrmRoadProvider

    origin, (ground, _) = _make_stops_and_origin()
    provider = OsrmRoadProvider(base_url="http://mock")
    payload = {
        "code": "Ok",
        "durations": [[0.0, 3601.0], [3601.0, 0.0]],
        "distances": [[0.0, 80000.0], [80000.0, 0.0]],
    }
    import app.integrations.roads as roads

    original_get = roads.httpx.get
    roads.httpx.get = lambda *args, **kw: _FakeOSRMResponse(payload)
    try:
        estimates = provider.estimate(origin, [ground])
    finally:
        roads.httpx.get = original_get
    assert estimates[0].travel_minutes == 61


def test_round_19_osrm_mixed_modes_honest_sources():
    """GROUND 段 = osrm-road；AIR 段回退大圆估算并如实标注来源。"""
    from app.integrations.roads import OsrmRoadProvider

    origin, stops = _make_stops_and_origin()  # [GROUND, AIR]
    provider = OsrmRoadProvider(base_url="http://mock")
    payload = {
        "code": "Ok",
        "durations": [[0.0, 3600.0, 7200.0], [3600.0, 0.0, 3600.0], [7200.0, 3600.0, 0.0]],
        "distances": [[0.0, 80000.0, 160000.0], [80000.0, 0.0, 80000.0], [160000.0, 80000.0, 0.0]],
    }
    import app.integrations.roads as roads

    original_get = roads.httpx.get
    roads.httpx.get = lambda *args, **kw: _FakeOSRMResponse(payload)
    try:
        estimates = provider.estimate(origin, stops)
    finally:
        roads.httpx.get = original_get

    ground_leg = next(e for e in estimates if e.to_visit_id == stops[0].visit_id)
    air_leg = next(e for e in estimates if e.to_visit_id == stops[1].visit_id)
    assert ground_leg.source == "osrm-road"
    assert air_leg.source == "haversine-estimate"
    assert ground_leg.travel_minutes == 60


# ---------------------------------------------------------------------------
# 轮 20–22：签证覆盖的边界
# ---------------------------------------------------------------------------


def test_round_20_visa_override_upsert_updates_single_row():
    entry_v1 = {"label": "德国", "business_entry": "申根商务 v1"}
    entry_v2 = {"label": "德国", "business_entry": "申根商务 v2", "review_hint": "复核"}
    try:
        with org_session(UUID(ORG_A)) as session:
            first = visa_catalog_override_service.upsert_override(
                session, ORG_A, country_code="DE", entry=entry_v1, actor_user_id=USER_A
            )
            second = visa_catalog_override_service.upsert_override(
                session, ORG_A, country_code="DE", entry=entry_v2, actor_user_id=USER_A
            )
            assert first.id == second.id  # 同国别覆盖是更新，不是第二行
            assert second.entry["business_entry"] == "申根商务 v2"
            assert second.updated_by_user_id == USER_A
            rows = session.execute(
                text("SELECT count(*) FROM visa_catalog_overrides WHERE country_code='DE'")
            ).scalar()
            assert rows == 1
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(text("DELETE FROM visa_catalog_overrides WHERE country_code='DE'"))


def test_round_21_visa_override_delete_unknown_raises_not_found():
    with org_session(UUID(ORG_A)) as session:
        with pytest.raises(NotFound):
            visa_catalog_override_service.delete_override(
                session, ORG_A, country_code="QQ"
            )


def test_round_22_visa_override_normalizes_code_and_keeps_extra_keys():
    try:
        with org_session(UUID(ORG_A)) as session:
            override = visa_catalog_override_service.upsert_override(
                session, ORG_A, country_code="de",
                entry={"label": "德国", "business_entry": "申根", "processing_days": 10},
            )
            assert override.country_code == "DE"
            assert override.entry["processing_days"] == 10  # extra 键原样保留
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(text("DELETE FROM visa_catalog_overrides WHERE country_code='DE'"))


# ---------------------------------------------------------------------------
# 轮 23–24：CSV 导出的边界
# ---------------------------------------------------------------------------


def test_round_23_csv_empty_trip_header_only(client: TestClient):
    trip_id, place_id = _expense_trip(client, "轮23")
    try:
        export = client.get(f"/v1/trips/{trip_id}/expenses:export", headers=headers())
        rows = list(csv.reader(io.StringIO(export.text)))
        assert rows == [["occurred_on", "category", "amount", "currency", "note", "receipt_ref"]]
    finally:
        _cleanup_trip(trip_id, place_id)


def test_round_24_csv_quotes_and_commas_roundtrip(client: TestClient):
    trip_id, place_id = _expense_trip(client, "轮24")
    try:
        created = client.post(
            f"/v1/trips/{trip_id}/expenses",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={
                "category": "OTHER",
                "amount": "12.00",
                "currency": "USD",
                "occurred_on": "2026-11-20",
                "note": '含"引号", 含逗号',
            },
        )
        assert created.status_code == 201, created.text
        export = client.get(f"/v1/trips/{trip_id}/expenses:export", headers=headers())
        rows = list(csv.reader(io.StringIO(export.text)))
        assert rows[1][4] == '含"引号", 含逗号'  # csv.reader 读回与原文一致
    finally:
        _cleanup_trip(trip_id, place_id)


def _expense_trip(client: TestClient, name: str) -> tuple[str, str]:
    place = client.post(
        "/v1/places",
        headers=headers(),
        json={"name": f"{name}客户", "lat": 48.13, "lon": 11.58, "city": "Munich"},
    )
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"{name}出行",
            "window_start": "2026-11-20T09:00:00+08:00",
            "window_end": "2026-11-22T18:00:00+08:00",
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    client.post(
        f"/v1/trips/{trip.json()['id']}/visits",
        headers=headers(),
        json={"place_id": place.json()["id"], "title": f"{name}拜访", "service_minutes": 60},
    )
    return trip.json()["id"], place.json()["id"]


# ---------------------------------------------------------------------------
# 轮 25–26：OCR 提取边界
# ---------------------------------------------------------------------------


def test_round_25_ocr_empty_image_raises():
    from app.services.receipt_ocr_service import OcrUnavailable

    if not receipt_ocr_service.ocr_available():
        pytest.skip("tesseract 未安装")
    with pytest.raises(OcrUnavailable):
        receipt_ocr_service.extract_receipt_fields(b"")


def test_round_26_ocr_chinese_date_format():
    text = "发票日期：2026年11月12日\n合计 ￥128.00\n"
    dates = receipt_ocr_service._dates(text)
    amounts = receipt_ocr_service._amounts(text)
    assert dates and "2026年11月12日" in dates[0]
    assert any("128.00" in a for a in amounts)


# ---------------------------------------------------------------------------
# 轮 27–28：字段加密 key 解析的边界
# ---------------------------------------------------------------------------


def test_round_27_missing_key_file_raises_with_hint(monkeypatch):
    from app.config import get_settings
    from app.models.encrypted import EncryptedText
    from cryptography.fernet import Fernet

    settings = get_settings()
    monkeypatch.setattr(settings, "field_encryption_key", None, raising=False)
    monkeypatch.setattr(
        settings, "field_encryption_key_file", "/nonexistent/key", raising=False
    )
    with pytest.raises(RuntimeError) as excinfo:
        EncryptedText().process_bind_param("x", None)
    # FileNotFoundError 也会冒出；统一语义：要么文件错误提示，要么 key 校验提示
    assert "KEY" in str(excinfo.value) or "No such file" in str(excinfo.value)
    assert Fernet is not None  # 依赖在场


def test_round_28_explicit_key_param_wins_over_file(tmp_path, monkeypatch):
    from app.config import get_settings
    from app.models.encrypted import EncryptedText
    from cryptography.fernet import Fernet

    explicit = Fernet.generate_key().decode()
    file_key = Fernet.generate_key().decode()
    key_file = tmp_path / "k"
    key_file.write_text(file_key, encoding="utf-8")
    settings = get_settings()
    monkeypatch.setattr(settings, "field_encryption_key", None, raising=False)
    monkeypatch.setattr(settings, "field_encryption_key_file", str(key_file), raising=False)

    column = EncryptedText(key=explicit)  # 显式 key（列定义处）最高优先级
    token = column.process_bind_param("显式优先", None)
    assert Fernet(explicit.encode()).decrypt(token.encode()).decode() == "显式优先"
    # 且文件 key 解不开：证明用的不是文件
    from cryptography.fernet import InvalidToken

    with pytest.raises(InvalidToken):
        Fernet(file_key.encode()).decrypt(token.encode())
