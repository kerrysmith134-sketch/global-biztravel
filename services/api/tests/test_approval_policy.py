"""N1 审批策略验收：按风险等级要求最低批准角色。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session, owner_session
from app.main import app
from app.models.identity import Membership, User
from app.models.proactive import ProactiveSuggestion
from app.models.reliability import OutboxEvent
from app.models.trip import Trip
from app.services import proactive_service
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ADMIN_USER = "55555555-5555-5555-5555-555555555555"  # mia（ADMIN）


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(user: str = ADMIN_USER) -> dict[str, str]:
    return {"X-Org-Id": ORG_A, "X-User-Id": user}


def test_approval_policy_requires_higher_role_for_high_severity(client: TestClient) -> None:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"N1 审批策略·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(hours=48)).isoformat(),
            "window_end": (now + timedelta(days=4)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]

    traveler_id = uuid.uuid4()
    email = f"n1-traveler-{uuid.uuid4().hex[:8]}@anari.example"
    policy_id: str | None = None
    try:
        with org_session(UUID(ORG_A)) as session:
            proactive_service.scan_trip(session, UUID(ORG_A), UUID(trip_id))
            suggestion = session.scalars(
                select(ProactiveSuggestion).where(ProactiveSuggestion.trip_id == UUID(trip_id))
            ).one()
            assert suggestion.status == "PENDING"
            assert suggestion.severity == "HIGH"

        # 创建一个 TRAVELER 身份的临时用户（用于验证角色门）。
        with owner_session() as session:
            session.add(
                User(
                    id=traveler_id,
                    email=email,
                    display_name="N1 Traveler",
                )
            )
            session.flush()
            session.add(
                Membership(organization_id=UUID(ORG_A), user_id=traveler_id, role="TRAVELER")
            )

        policy = client.post(
            "/v1/approval-policies",
            headers=headers(),
            json={
                "subject_type": "proactive_suggestion",
                "min_severity": "MEDIUM",
                "required_role": "ADMIN",
                "enabled": True,
            },
        )
        assert policy.status_code == 201, policy.text
        policy_id = policy.json()["id"]

        denied = client.post(
            f"/v1/proactive/suggestions/{suggestion.id}:approve",
            headers=headers(str(traveler_id)),
        )
        assert denied.status_code == 403, denied.text
        assert denied.json()["error"]["details"]["required_role"] == "ADMIN"

        allowed = client.post(
            f"/v1/proactive/suggestions/{suggestion.id}:approve", headers=headers()
        )
        assert allowed.status_code == 200, allowed.text
        assert allowed.json()["status"] == "EXECUTED"
    finally:
        if policy_id:
            client.delete(f"/v1/approval-policies/{policy_id}", headers=headers())
        with owner_session() as session:
            session.execute(delete(User).where(User.id == traveler_id))
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(OutboxEvent).where(OutboxEvent.aggregate_id == UUID(trip_id))
            )
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
