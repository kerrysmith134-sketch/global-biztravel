"""CRM 同步任务：文件适配器标 PREPARED，不伪造外部写入；不可用显式失败。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from app.config import get_settings
from app.db import org_session
from app.main import app
from app.models.crm import CrmSyncJob
from app.models.geo import Place
from app.models.reliability import BackgroundJob
from app.models.trip import Trip
from app.services import crm_service, job_service
from app.workers import tasks
from app.workers.tasks import TaskFailed  # noqa: F401  （异常类型用于结算路径）
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


def setup(client: TestClient) -> tuple[str, str, str, str]:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"CRM 同步·{uuid.uuid4().hex[:8]}",
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
        json={"name": f"CRM 地点·{uuid.uuid4().hex[:6]}", "city": "Berlin", "country_code": "DE", "lat": 52.52, "lon": 13.405},
    )
    assert place.status_code == 201
    place_id = place.json()["id"]
    visit = client.post(
        f"/v1/trips/{trip_id}/visits",
        headers=headers(),
        json={"place_id": place_id, "title": "CRM 拜访", "service_minutes": 60},
    )
    assert visit.status_code == 201
    record = client.post(
        f"/v1/visits/{visit.json()['id']}/records",
        headers=headers(),
        json={
            "client_record_id": str(uuid.uuid4()),
            "outcome": "MET",
            "notes": "客户确认续约",
            "client_updated_at": now.isoformat(),
            "photos": [],
            "follow_up_tasks": [],
        },
    )
    assert record.status_code == 201, record.text
    return trip_id, place_id, record.json()["id"], visit.json()["id"]


def cleanup(trip_id: str, place_id: str, job_ids: list[str]) -> None:
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
        session.execute(delete(Place).where(Place.id == UUID(place_id)))
        for job_id in job_ids:
            session.execute(delete(BackgroundJob).where(BackgroundJob.id == UUID(job_id)))


def run_crm_job(crm_job_id: str) -> dict:
    with org_session(UUID(ORG_A)) as session:
        crm_job = crm_service.get_job(session, ORG_A, crm_job_id)
        job = session.get(BackgroundJob, crm_job.job_id) if crm_job.job_id else None
        if job is None:
            raise AssertionError("CRM 任务缺少背景任务")
        try:
            return tasks.handle_crm_sync(session, job)
        except TaskFailed as exc:
            # 与 Worker.execute_one 一致：不可重试失败保留处理器副作用并结算任务。
            job_service.fail(session, job.id, error=exc.message, retryable=False)
            return {"status": "TASK_FAILED", "message": exc.message}


def test_crm_sync_file_adapter_and_idempotency(client: TestClient) -> None:
    trip_id, place_id, record_id, visit_id = setup(client)
    job_ids: list[str] = []
    written: list[Path] = []
    try:
        accepted = client.post(
            f"/v1/visit-records/{record_id}/crm-sync",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert accepted.status_code == 202, accepted.text
        body = accepted.json()
        assert body["status"] == "QUEUED"
        if body["job_id"]:
            job_ids.append(body["job_id"])

        duplicate = client.post(
            f"/v1/visit-records/{record_id}/crm-sync",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert duplicate.status_code == 202
        assert duplicate.json()["id"] == body["id"]

        result = run_crm_job(body["id"])
        assert result["status"] == "PREPARED"
        assert result["external_id"].startswith("file:")
        path = Path(result["external_id"].removeprefix("file:"))
        written.append(path)
        assert path.exists()
        assert "客户确认续约" in path.read_text(encoding="utf-8")

        status_resp = client.get(f"/v1/crm-sync-jobs/{body['id']}", headers=headers())
        assert status_resp.status_code == 200
        assert status_resp.json()["status"] == "PREPARED"

        stolen = client.get(
            f"/v1/crm-sync-jobs/{body['id']}", headers=headers(ORG_B, USER_B)
        )
        assert stolen.status_code == 404
    finally:
        for path in written:
            path.unlink(missing_ok=True)
        cleanup(trip_id, place_id, job_ids)


def test_crm_unavailable_fails_explicitly(client: TestClient, monkeypatch) -> None:
    trip_id, place_id, record_id, visit_id = setup(client)
    job_ids: list[str] = []
    try:
        settings = get_settings()
        monkeypatch.setattr(settings, "crm_provider", "unavailable", raising=False)
        accepted = client.post(
            f"/v1/visit-records/{record_id}/crm-sync",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert accepted.status_code == 202, accepted.text
        body = accepted.json()
        if body["job_id"]:
            job_ids.append(body["job_id"])

        result = run_crm_job(body["id"])
        assert result["status"] == "TASK_FAILED"

        with org_session(UUID(ORG_A)) as session:
            row = session.get(CrmSyncJob, UUID(body["id"]))
            assert row is not None
            assert row.status == "FAILED"
            assert "未配置" in (row.error or "")
    finally:
        cleanup(trip_id, place_id, job_ids)
