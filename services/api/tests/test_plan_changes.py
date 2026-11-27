"""统一计划变更入口验收（PRD §6 / AT-010）。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.geo import Place
from app.models.planning import PlanVersion
from app.models.reliability import BackgroundJob
from app.models.trip import Trip
from app.models.workflow import WorkflowInstance
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


def setup_active_plan(client: TestClient) -> tuple[str, str, str, str]:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"计划变更·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=5)).isoformat(),
            "window_end": (now + timedelta(days=8)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]
    place = client.post(
        "/v1/places",
        headers=headers(),
        json={"name": f"变更地点·{uuid.uuid4().hex[:6]}", "city": "Berlin", "country_code": "DE", "lat": 52.52, "lon": 13.405},
    )
    assert place.status_code == 201, place.text
    place_id = place.json()["id"]
    visit = client.post(
        f"/v1/trips/{trip_id}/visits",
        headers=headers(),
        json={"place_id": place_id, "title": "变更测试拜访", "service_minutes": 60},
    )
    assert visit.status_code == 201, visit.text
    visit_id = visit.json()["id"]

    with org_session(UUID(ORG_A)) as session:
        trip_row = session.get(Trip, UUID(trip_id))
        version = PlanVersion(
            organization_id=UUID(ORG_A),
            trip_id=UUID(trip_id),
            version_number=1,
            status="ACTIVE",
            problem_hash="sha256:planchange",
            solver="heuristic",
            objective_value=1.0,
            solution_status="FEASIBLE",
            trip_version=trip_row.version,
            validation={"feasible": True},
            unplaced=[],
        )
        session.add(version)
        session.flush()
        trip_row.active_plan_version_id = version.id
        trip_row.status = "ACTIVE"
    return trip_id, place_id, visit_id, str(version.id)


def cleanup(trip_id: str, place_id: str, job_ids: list[str]) -> None:
    with org_session(UUID(ORG_A)) as session:
        from app.models.reliability import BackgroundJob

        session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
        session.execute(delete(Place).where(Place.id == UUID(place_id)))
        for job_id in job_ids:
            session.execute(delete(BackgroundJob).where(BackgroundJob.id == UUID(job_id)))


def test_plan_change_updates_input_and_enqueues_new_plan(client: TestClient) -> None:
    trip_id, place_id, visit_id, plan_id = setup_active_plan(client)
    job_ids: list[str] = []
    try:
        trip = client.get(f"/v1/trips/{trip_id}", headers=headers()).json()
        new_start = (datetime.now(UTC) + timedelta(days=6)).isoformat()
        resp = client.post(
            f"/v1/trips/{trip_id}/plan-changes",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={
                "base_plan_version_id": plan_id,
                "base_trip_version": trip["version"],
                "source": "MAP",
                "operation_type": "CHANGE_VISIT_TIME",
                "payload": {"visit_id": visit_id, "new_window_start": new_start},
                "reason": "客户要求改到上午",
            },
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == "ACCEPTED"
        job_ids.append(body["optimization_job_id"])
        assert body["trip_version"] > trip["version"]

        with org_session(UUID(ORG_A)) as session:
            workflow = session.get(WorkflowInstance, UUID(body["change_request_id"]))
            assert workflow is not None
            assert workflow.workflow_type == "PLAN_CHANGE"
            assert workflow.payload["operation_type"] == "CHANGE_VISIT_TIME"
            original_job = session.get(BackgroundJob, UUID(body["optimization_job_id"]))
            assert original_job is not None
            original_job.status = "FAILED"
            original_job.finished_at = datetime.now(UTC)

        retry_key = str(uuid.uuid4())
        retried = client.post(
            f"/v1/plan-changes/{body['change_request_id']}:retry",
            headers={**headers(), "Idempotency-Key": retry_key},
        )
        assert retried.status_code == 202, retried.text
        retried_body = retried.json()
        assert retried_body["change_request_id"] == body["change_request_id"]
        assert retried_body["trip_version"] == body["trip_version"]
        assert retried_body["optimization_job_id"] != body["optimization_job_id"]
        job_ids.append(retried_body["optimization_job_id"])

        # Replaying the same key returns the same response; a separate concurrent retry
        # reuses the in-flight job and does not create an orphaned second job.
        retry_replay = client.post(
            f"/v1/plan-changes/{body['change_request_id']}:retry",
            headers={**headers(), "Idempotency-Key": retry_key},
        )
        assert retry_replay.status_code == 202, retry_replay.text
        assert retry_replay.json()["optimization_job_id"] == retried_body["optimization_job_id"]

        retry_reuse = client.post(
            f"/v1/plan-changes/{body['change_request_id']}:retry",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert retry_reuse.status_code == 202, retry_reuse.text
        assert retry_reuse.json()["optimization_job_id"] == retried_body["optimization_job_id"]

        with org_session(UUID(ORG_A)) as session:
            workflow = session.get(WorkflowInstance, UUID(body["change_request_id"]))
            assert workflow is not None
            assert workflow.job_id == UUID(retried_body["optimization_job_id"])
            assert workflow.payload["retry_count"] == 1
            assert len(workflow.payload["retry_history"]) == 1
            retried_job = session.get(BackgroundJob, UUID(retried_body["optimization_job_id"]))
            assert retried_job is not None and retried_job.status == "PENDING"

        # A later Trip input mutation makes the old Plan Change workflow non-retryable.
        with org_session(UUID(ORG_A)) as session:
            retried_job = session.get(BackgroundJob, UUID(retried_body["optimization_job_id"]))
            trip_row = session.get(Trip, UUID(trip_id))
            assert retried_job is not None and trip_row is not None
            retried_job.status = "FAILED"
            retried_job.finished_at = datetime.now(UTC)
            trip_row.version += 1

        stale_retry = client.post(
            f"/v1/plan-changes/{body['change_request_id']}:retry",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert stale_retry.status_code == 409, stale_retry.text
        assert stale_retry.json()["error"]["code"] == "PLAN_VERSION_CONFLICT"

        visits = client.get(f"/v1/trips/{trip_id}/visits", headers=headers()).json()
        target = next(item for item in visits if item["id"] == visit_id)
        assert target["time_window_start"] is not None

        # Even with a freshly read Trip Version, the old ACTIVE plan is stale because
        # its immutable trip_version still points to the inputs before the first mutation.
        latest_trip = client.get(f"/v1/trips/{trip_id}", headers=headers()).json()
        stale_active_plan = client.post(
            f"/v1/trips/{trip_id}/plan-changes",
            headers=headers(),
            json={
                "base_plan_version_id": plan_id,
                "base_trip_version": latest_trip["version"],
                "source": "CALENDAR",
                "operation_type": "CANCEL_VISIT",
                "payload": {"visit_id": visit_id},
            },
        )
        assert stale_active_plan.status_code == 409, stale_active_plan.text
        assert stale_active_plan.json()["error"]["code"] == "PLAN_VERSION_CONFLICT"

        # 用旧版本再提交：必须 409，不能悄悄基于旧输入生成计划。
        stale = client.post(
            f"/v1/trips/{trip_id}/plan-changes",
            headers=headers(),
            json={
                "base_plan_version_id": plan_id,
                "base_trip_version": trip["version"],
                "source": "CALENDAR",
                "operation_type": "CANCEL_VISIT",
                "payload": {"visit_id": visit_id},
            },
        )
        assert stale.status_code == 409
        assert stale.json()["error"]["code"] == "PLAN_VERSION_CONFLICT"
    finally:
        cleanup(trip_id, place_id, job_ids)
