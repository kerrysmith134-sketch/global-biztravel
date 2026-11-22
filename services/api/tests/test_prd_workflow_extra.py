"""PRD 缺口补验：Trip 状态机、任务取消、证据/外部动作、同步操作、异步纪要、集成连接。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.geo import Place
from app.models.proactive import Approval
from app.models.reliability import BackgroundJob, OutboxEvent
from app.models.trip import Trip
from app.models.workflow import AgentRun, Evidence, ExternalAction, IntegrationConnection, SyncOperation
from app.services import job_service, proactive_service
from app.workers import tasks
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"
USER_A2 = "55555555-5555-5555-5555-555555555555"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(user: str = USER_A) -> dict[str, str]:
    return {"X-Org-Id": ORG_A, "X-User-Id": user}


def create_trip(client: TestClient, *, hours: int = 240, user: str = USER_A) -> str:
    now = datetime.now(UTC)
    resp = client.post(
        "/v1/trips",
        headers=headers(user),
        json={
            "name": f"PRD 缺口·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(hours=hours)).isoformat(),
            "window_end": (now + timedelta(hours=hours + 72)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def create_visit(client: TestClient, trip_id: str) -> tuple[str, str]:
    place = client.post(
        "/v1/places",
        headers=headers(),
        json={"name": f"缺口地点·{uuid.uuid4().hex[:6]}", "city": "Berlin", "country_code": "DE", "lat": 52.52, "lon": 13.405},
    )
    assert place.status_code == 201, place.text
    visit = client.post(
        f"/v1/trips/{trip_id}/visits",
        headers=headers(),
        json={"place_id": place.json()["id"], "title": "缺口拜访", "service_minutes": 60},
    )
    assert visit.status_code == 201, visit.text
    return place.json()["id"], visit.json()["id"]


def drop_trip(trip_id: str, place_id: str | None = None) -> None:
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(OutboxEvent).where(OutboxEvent.aggregate_id == UUID(trip_id)))
        session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
        if place_id:
            session.execute(delete(Place).where(Place.id == UUID(place_id)))


def test_trip_hold_resume_complete(client: TestClient) -> None:
    trip_id = create_trip(client)
    try:
        held = client.post(f"/v1/trips/{trip_id}/hold", headers=headers())
        assert held.status_code == 200, held.text
        assert held.json()["status"] == "HOLD"

        resumed = client.post(f"/v1/trips/{trip_id}/resume", headers=headers())
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["status"] == "DRAFT"

        completed = client.post(f"/v1/trips/{trip_id}/complete", headers=headers())
        assert completed.status_code == 200
        assert completed.json()["status"] == "COMPLETED"

        again = client.post(f"/v1/trips/{trip_id}/resume", headers=headers())
        assert again.status_code == 409
    finally:
        drop_trip(trip_id)


def test_pending_job_can_be_cancelled(client: TestClient) -> None:
    trip_id = create_trip(client)
    job_id: str | None = None
    try:
        generated = client.post(f"/v1/trips/{trip_id}/plans:generate", headers=headers())
        assert generated.status_code == 202, generated.text
        job_id = generated.json()["job_id"]

        cancelled = client.post(f"/v1/jobs/{job_id}:cancel", headers=headers())
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "CANCELLED"

        repeat = client.post(f"/v1/jobs/{job_id}:cancel", headers=headers())
        assert repeat.status_code == 200
        assert repeat.json()["status"] == "CANCELLED"
    finally:
        with org_session(UUID(ORG_A)) as session:
            if job_id:
                session.execute(delete(BackgroundJob).where(BackgroundJob.id == UUID(job_id)))
        drop_trip(trip_id)


def test_evidence_and_external_action_are_persisted(client: TestClient) -> None:
    trip_id = create_trip(client, hours=48, user=USER_A)
    suggestion_id: str | None = None
    try:
        with org_session(UUID(ORG_A)) as session:
            proactive_service.scan_trip(session, UUID(ORG_A), UUID(trip_id))
            suggestion = session.scalars(
                select(proactive_service.ProactiveSuggestion).where(
                    proactive_service.ProactiveSuggestion.trip_id == UUID(trip_id)
                )
            ).one()
            suggestion_id = str(suggestion.id)

        evidence = client.get(
            "/v1/evidence",
            headers=headers(),
            params={"subject_type": "proactive_suggestion", "subject_id": suggestion_id},
        )
        assert evidence.status_code == 200, evidence.text
        assert len(evidence.json()) >= 1

        approved = client.post(
            f"/v1/proactive/suggestions/{suggestion_id}:approve", headers=headers()
        )
        assert approved.status_code == 200, approved.text

        with org_session(UUID(ORG_A)) as session:
            action_count = session.scalar(
                select(func.count()).select_from(ExternalAction).where(
                    ExternalAction.subject_id == UUID(suggestion_id)
                )
            )
        assert action_count == 1
    finally:
        with org_session(UUID(ORG_A)) as session:
            if suggestion_id:
                session.execute(delete(Evidence).where(Evidence.subject_id == UUID(suggestion_id)))
                session.execute(delete(ExternalAction).where(ExternalAction.subject_id == UUID(suggestion_id)))
                session.execute(delete(Approval).where(Approval.subject_id == UUID(suggestion_id)))
        drop_trip(trip_id)


def test_sync_operation_is_recorded_per_item(client: TestClient) -> None:
    trip_id = create_trip(client)
    place_id, visit_id = create_visit(client, trip_id)
    operation_id = str(uuid.uuid4())
    try:
        resp = client.post(
            "/v1/sync:push",
            headers=headers(),
            json={
                "device_id": "prd-gap-device",
                "items": [
                    {
                        "item_id": operation_id,
                        "entity": "visit_record",
                        "operation": "upsert",
                        "payload": {
                            "visit_id": visit_id,
                            "client_record_id": operation_id,
                            "outcome": "MET",
                            "notes": "同步审计",
                            "client_updated_at": datetime.now(UTC).isoformat(),
                            "photos": [],
                            "follow_up_tasks": [],
                        },
                        "client_updated_at": datetime.now(UTC).isoformat(),
                    }
                ],
            },
        )
        assert resp.status_code == 200, resp.text
        with org_session(UUID(ORG_A)) as session:
            row = session.scalars(
                select(SyncOperation).where(SyncOperation.operation_id == operation_id)
            ).one()
        assert row.status == "APPLIED"
        assert row.operation_type == "visit_record:upsert"
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(SyncOperation).where(SyncOperation.operation_id == operation_id))
        drop_trip(trip_id, place_id)


def test_async_minutes_job_and_agent_run(client: TestClient) -> None:
    trip_id = create_trip(client)
    place_id, visit_id = create_visit(client, trip_id)
    job_id: str | None = None
    started = datetime.now(UTC) - timedelta(seconds=2)
    try:
        record = client.post(
            f"/v1/visits/{visit_id}/records",
            headers=headers(),
            json={
                "client_record_id": str(uuid.uuid4()),
                "outcome": "MET",
                "notes": "异步纪要测试",
                "client_updated_at": datetime.now(UTC).isoformat(),
                "photos": [],
                "follow_up_tasks": [{"title": "跟进报价", "due_date": None}],
            },
        )
        assert record.status_code == 201, record.text

        accepted = client.post(f"/v1/visits/{visit_id}/minutes:jobs", headers=headers())
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["job_id"]

        with org_session(UUID(ORG_A)) as session:
            job = job_service.get_job(session, ORG_A, job_id)
            result = tasks.handle_draft_minutes(session, job)
        assert result["summary"]
        assert result["draft_id"]
        with org_session(UUID(ORG_A)) as session:
            runs = session.scalar(
                select(func.count()).select_from(AgentRun).where(
                    AgentRun.organization_id == UUID(ORG_A),
                    AgentRun.created_at >= started,
                )
            )
        assert runs >= 1
    finally:
        with org_session(UUID(ORG_A)) as session:
            if job_id:
                session.execute(delete(BackgroundJob).where(BackgroundJob.id == UUID(job_id)))
            # This service creates an unbound meeting-minutes run; restrict cleanup
            # to its own agent type so unrelated Planner provenance is never swept
            # by a broad wall-clock predicate.
            session.execute(
                delete(AgentRun).where(
                    AgentRun.organization_id == UUID(ORG_A),
                    AgentRun.agent_type == "MEETING_MINUTES_DRAFT",
                    AgentRun.created_at >= started,
                )
            )
        drop_trip(trip_id, place_id)


def test_integration_connection_crud(client: TestClient) -> None:
    connection_id: str | None = None
    try:
        created = client.post(
            "/v1/integrations/connections",
            headers=headers(),
            json={
                "provider": "stub-visa",
                "capability": "visa_requirements",
                "status": "ACTIVE",
                "config": {"source": "offline"},
            },
        )
        assert created.status_code == 201, created.text
        connection_id = created.json()["id"]
        listed = client.get("/v1/integrations/connections", headers=headers())
        assert any(item["id"] == connection_id for item in listed.json())
        removed = client.delete(
            f"/v1/integrations/connections/{connection_id}", headers=headers()
        )
        assert removed.status_code == 204
    finally:
        with org_session(UUID(ORG_A)) as session:
            if connection_id:
                session.execute(
                    delete(IntegrationConnection).where(
                        IntegrationConnection.id == UUID(connection_id)
                    )
                )
