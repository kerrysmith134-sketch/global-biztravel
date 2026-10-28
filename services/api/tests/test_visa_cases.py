"""签证案件与入境评估（PRD 方向二）验收。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.trip import Trip
from app.models.visa import EntryAssessment, VisaCase
from app.services import proactive_service
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

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


def create_trip(client: TestClient, *, countries: list[str], hours: int = 240) -> str:
    now = datetime.now(UTC)
    resp = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"签证案件·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(hours=hours)).isoformat(),
            "window_end": (now + timedelta(hours=hours + 48)).isoformat(),
            "countries": countries,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def drop_trip(trip_id: str) -> None:
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))


def test_visa_case_lifecycle_and_assessments(client: TestClient) -> None:
    trip_id = create_trip(client, countries=["DE", "NL"])
    try:
        empty = client.get(f"/v1/trips/{trip_id}/visa-cases", headers=headers())
        assert empty.status_code == 200
        assert empty.json() == []

        created = client.post(
            f"/v1/trips/{trip_id}/visa-cases",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={},
        )
        assert created.status_code == 201, created.text
        cases = created.json()
        assert {item["country_code"] for item in cases} == {"DE", "NL"}
        assert all(item["status"] == "NEW" for item in cases)
        assert all(len(item["checklist"]) >= 6 for item in cases)

        de_case = next(item for item in cases if item["country_code"] == "DE")
        preparing = client.patch(
            f"/v1/visa-cases/{de_case['id']}",
            headers=headers(),
            json={
                "status": "PREPARING",
                "notes": "等待邀请函",
                "checklist": [
                    {"key": "passport", "label": "护照", "status": "READY"},
                    {"key": "invitation_letter", "label": "邀请函", "status": "MISSING"},
                ],
            },
        )
        assert preparing.status_code == 200, preparing.text
        assert preparing.json()["status"] == "PREPARING"
        assert preparing.json()["checklist"][0]["status"] == "READY"

        invalid = client.patch(
            f"/v1/visa-cases/{de_case['id']}",
            headers=headers(),
            json={"status": "APPROVED"},
        )
        assert invalid.status_code == 409

        submitted = client.patch(
            f"/v1/visa-cases/{de_case['id']}",
            headers=headers(),
            json={"status": "SUBMITTED"},
        )
        assert submitted.status_code == 200
        assert submitted.json()["submitted_at"] is not None
        approved = client.patch(
            f"/v1/visa-cases/{de_case['id']}",
            headers=headers(),
            json={"status": "APPROVED"},
        )
        assert approved.status_code == 200
        assert approved.json()["decision_at"] is not None

        assessments = client.post(
            f"/v1/trips/{trip_id}/entry-assessments:refresh", headers=headers()
        )
        assert assessments.status_code == 200, assessments.text
        assert {item["country_code"] for item in assessments.json()} == {"DE", "NL"}
        assert all(item["manual_review_required"] is True for item in assessments.json())
        assert all(item["source"] == "offline-visa-catalog" for item in assessments.json())

        stolen = client.get(
            f"/v1/trips/{trip_id}/visa-cases", headers=headers(ORG_B, USER_B)
        )
        assert stolen.status_code == 404
    finally:
        drop_trip(trip_id)


def test_approving_visa_suggestion_creates_cases(client: TestClient) -> None:
    trip_id = create_trip(client, countries=["DE"], hours=48)
    try:
        with org_session(UUID(ORG_A)) as session:
            proactive_service.scan_trip(session, UUID(ORG_A), UUID(trip_id))
            suggestion = session.scalars(
                select(proactive_service.ProactiveSuggestion).where(
                    proactive_service.ProactiveSuggestion.trip_id == UUID(trip_id)
                )
            ).one()
        approved = client.post(
            f"/v1/proactive/suggestions/{suggestion.id}:approve", headers=headers()
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["execution"]["status"] == "PREPARED"
        with org_session(UUID(ORG_A)) as session:
            cases = list(
                session.scalars(
                    select(VisaCase).where(VisaCase.trip_id == UUID(trip_id))
                )
            )
            assessments = session.scalar(
                select(EntryAssessment).where(EntryAssessment.trip_id == UUID(trip_id))
            )
        assert len(cases) == 1
        assert cases[0].country_code == "DE"
        # 评估由显式刷新产生；approve 只创建案件，不伪造入境结论。
        assert assessments is None
    finally:
        drop_trip(trip_id)
