"""当地生活指南（PRD 方向五）验收：离线目录 + 行程城市匹配 + 显式缺口。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.geo import Place
from app.models.trip import Trip
from fastapi.testclient import TestClient
from sqlalchemy import delete

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
USER_A = "22222222-2222-2222-2222-222222222222"
USER_B = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(org: str = ORG_A, user: str = USER_A) -> dict[str, str]:
    return {"X-Org-Id": org, "X-User-Id": user}


def test_local_guide_matches_trip_countries_and_cities(client: TestClient) -> None:
    now = datetime.now(UTC)
    place_ids: list[str] = []
    trip_id: str | None = None

    def place(city: str, country: str) -> str:
        resp = client.post(
            "/v1/places",
            headers=headers(),
            json={
                "name": f"指南地点·{uuid.uuid4().hex[:6]}",
                "city": city,
                "country_code": country,
                "lat": 52.52,
                "lon": 13.405,
            },
        )
        assert resp.status_code == 201, resp.text
        place_ids.append(resp.json()["id"])
        return resp.json()["id"]

    try:
        berlin = place("Berlin", "DE")
        amsterdam = place("Amsterdam", "NL")
        trip = client.post(
            "/v1/trips",
            headers=headers(),
            json={
                "name": f"当地指南·{uuid.uuid4().hex[:8]}",
                "window_start": (now + timedelta(days=6)).isoformat(),
                "window_end": (now + timedelta(days=9)).isoformat(),
                "countries": ["DE", "NL", "XX"],
            },
        )
        assert trip.status_code == 201, trip.text
        trip_id = trip.json()["id"]

        for target, city in ((berlin, "柏林拜访"), (amsterdam, "阿姆斯特丹拜访")):
            visit = client.post(
                f"/v1/trips/{trip_id}/visits",
                headers=headers(),
                json={"place_id": target, "title": city, "service_minutes": 60},
            )
            assert visit.status_code == 201, visit.text

        guide = client.get(f"/v1/trips/{trip_id}/local-guide", headers=headers())
        assert guide.status_code == 200, guide.text
        body = guide.json()
        codes = [item["country_code"] for item in body["countries"]]
        assert codes == ["DE", "NL"]
        de = body["countries"][0]
        assert de["name"] == "德国"
        section_keys = {section["key"] for section in de["sections"]}
        assert {"etiquette", "payment", "transport"} <= section_keys
        # 城市贴士只覆盖拜访涉及的城市。
        assert [city["city"] for city in de["cities"]] == ["Berlin"]
        assert body["cities_covered"] == ["Amsterdam", "Berlin"]
        gap_codes = {gap["code"] for gap in body["data_gaps"]}
        assert {"LIVE_POI_NOT_CONNECTED", "WEATHER_NOT_CONNECTED"} <= gap_codes
        assert "COUNTRY_GUIDE_MISSING" in gap_codes  # XX 不在目录里

        stolen = client.get(
            f"/v1/trips/{trip_id}/local-guide", headers=headers(ORG_B, USER_B)
        )
        assert stolen.status_code == 404
    finally:
        with org_session(UUID(ORG_A)) as session:
            if trip_id:
                session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
            session.execute(
                delete(Place).where(Place.id.in_([UUID(item) for item in place_ids]))
            )
