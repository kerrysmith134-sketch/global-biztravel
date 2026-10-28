"""DST 端到端：跨夏令时的出行日归属与停靠时刻（N2）。"""

from __future__ import annotations

import uuid
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from app.db import org_session
from app.main import app
from app.models.geo import Place
from app.models.reliability import BackgroundJob
from app.models.trip import Trip
from app.services import job_service
from app.workers import tasks
from fastapi.testclient import TestClient
from sqlalchemy import delete

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers() -> dict[str, str]:
    return {"X-Org-Id": ORG_A, "X-User-Id": USER_A}


def test_spring_forward_keeps_local_day_assignment(client: TestClient) -> None:
    origin = client.post(
        "/v1/places",
        headers=headers(),
        json={
            "name": f"DST 出发地·{uuid.uuid4().hex[:6]}",
            "city": "Shanghai",
            "country_code": "CN",
            "lat": 31.2304,
            "lon": 121.4737,
            "timezone": "Asia/Shanghai",
        },
    )
    assert origin.status_code == 201, origin.text
    origin_id = origin.json()["id"]

    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"DST 出行·{uuid.uuid4().hex[:8]}",
            "origin_place_id": origin_id,
            "home_timezone": "Europe/Berlin",
            "window_start": "2027-03-27T00:00:00+01:00",
            "window_end": "2027-03-29T23:59:00+02:00",
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]
    job_id: str | None = None

    try:
        place = client.post(
            "/v1/places",
            headers=headers(),
            json={
                "name": f"DST 地点·{uuid.uuid4().hex[:6]}",
                "city": "Berlin",
                "country_code": "DE",
                "lat": 52.52,
                "lon": 13.405,
                "timezone": "Europe/Berlin",
            },
        )
        assert place.status_code == 201
        place_id = place.json()["id"]
        visit = client.post(
            f"/v1/trips/{trip_id}/visits",
            headers=headers(),
            json={
                "place_id": place_id,
                "title": "跨夏令时拜访",
                "service_minutes": 60,
                "movement_mode": "AIR",
                "time_window_start": "2027-03-28T14:30:00+02:00",
                "time_window_end": "2027-03-28T15:30:00+02:00",
            },
        )
        assert visit.status_code == 201, visit.text

        generated = client.post(f"/v1/trips/{trip_id}/plans:generate", headers=headers())
        assert generated.status_code == 202, generated.text
        job_id = generated.json()["job_id"]

        with org_session(UUID(ORG_A)) as session:
            job = session.get(BackgroundJob, UUID(job_id))
            assert job is not None
            result = tasks.handle_plan_trip(session, job)
            job_service.complete(session, job.id, result=result)
        plan_id = result["plan_version_id"]

        detail = client.get(f"/v1/trips/{trip_id}/plans/{plan_id}", headers=headers())
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["validation"]["feasible"] is True
        assert len(body["stops"]) == 1
        stop = body["stops"][0]
        # 3 月 28 日是切换日：14:30 已是 CEST（+02:00），且停靠归到当天而不是前一天。
        assert stop["day_index"] == 1
        assert stop["day_date"] == "2027-03-28"
        berlin = ZoneInfo("Europe/Berlin")
        arrive = datetime.fromisoformat(stop["arrive_at"]).astimezone(berlin)
        assert (arrive.hour, arrive.minute) == (15, 0)
        assert arrive.utcoffset().total_seconds() == 7200
        depart = datetime.fromisoformat(stop["depart_at"]).astimezone(berlin)
        assert (depart.hour, depart.minute) == (16, 0)
    finally:
        with org_session(UUID(ORG_A)) as session:
            if job_id:
                session.execute(delete(BackgroundJob).where(BackgroundJob.id == UUID(job_id)))
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
            session.execute(delete(Place).where(Place.id == UUID(origin_id)))
