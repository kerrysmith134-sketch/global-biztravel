"""PII 字段加密与留存脱敏验收。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.encrypted import EncryptedText
from app.models.geo import Place
from app.models.memory import TripMemorySummary
from app.models.records import VisitRecord
from app.models.trip import Trip
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import delete, update

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers() -> dict[str, str]:
    return {"X-Org-Id": ORG_A, "X-User-Id": USER_A}


def test_encrypted_text_round_trip_and_legacy_plaintext() -> None:
    key = Fernet.generate_key().decode()
    column = EncryptedText(key=key)
    plaintext = "cto@example.com"
    token = column.process_bind_param(plaintext, None)
    assert token is not None
    assert token != plaintext
    assert token.startswith("gAAAAA")
    assert column.process_result_value(token, None) == plaintext
    # 开启加密前的明文行仍可读（平滑迁移）。
    assert column.process_result_value(plaintext, None) == plaintext


def build_trip(client: TestClient) -> tuple[str, str, str]:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"留存验收·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=3)).isoformat(),
            "window_end": (now + timedelta(days=5)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]

    place = client.post(
        "/v1/places",
        headers=headers(),
        json={"name": f"留存地点·{uuid.uuid4().hex[:6]}", "city": "Berlin", "country_code": "DE", "lat": 52.52, "lon": 13.405},
    )
    assert place.status_code == 201, place.text
    place_id = place.json()["id"]

    visit = client.post(
        f"/v1/trips/{trip_id}/visits",
        headers=headers(),
        json={"place_id": place_id, "title": "留存测试拜访", "service_minutes": 60},
    )
    assert visit.status_code == 201, visit.text
    return trip_id, place_id, visit.json()["id"]


def add_record(client: TestClient, visit_id: str, notes: str) -> str:
    resp = client.post(
        f"/v1/visits/{visit_id}/records",
        headers=headers(),
        json={
            "client_record_id": str(uuid.uuid4()),
            "outcome": "MET",
            "notes": notes,
            "client_updated_at": datetime.now(UTC).isoformat(),
            "photos": ["photo-1"],
            "follow_up_tasks": [],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_retention_redacts_old_notes_and_memory(client: TestClient) -> None:
    trip_id, place_id, visit_id = build_trip(client)
    old_record_id = add_record(client, visit_id, "过期敏感笔记")
    recent_record_id = add_record(client, visit_id, "仍然有效的笔记")
    summary_id = uuid.uuid4()
    old = datetime.now(UTC) - timedelta(days=400)
    try:
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                update(VisitRecord)
                .where(VisitRecord.id == UUID(old_record_id))
                .values(created_at=old)
            )
            session.add(
                TripMemorySummary(
                    id=summary_id,
                    organization_id=UUID(ORG_A),
                    trip_id=UUID(trip_id),
                    kind="TRIP_REVIEW",
                    summary="过期记忆正文",
                    decisions=["敏感决策"],
                    follow_ups=["敏感待办"],
                    lessons=["敏感教训"],
                    embedding=[0.1] * 256,
                    confidence=0.8,
                    created_at=old,
                    source_ref=f"retention-test:{summary_id}",
                )
            )

        resp = client.post("/v1/admin:retention", headers=headers())
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["records_redacted"] >= 1
        assert body["summaries_redacted"] >= 1

        with org_session(UUID(ORG_A)) as session:
            record = session.get(VisitRecord, UUID(old_record_id))
            recent = session.get(VisitRecord, UUID(recent_record_id))
            summary = session.get(TripMemorySummary, summary_id)
        assert record is not None and record.notes == "[已按留存策略删除]"
        assert record.photos == []
        assert recent is not None and recent.notes == "仍然有效的笔记"
        assert summary is not None
        assert summary.summary == "[已按留存策略删除]"
        assert summary.decisions == [] and summary.embedding is None
        assert summary.valid_until is not None
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
            session.execute(delete(Place).where(Place.id == UUID(place_id)))
