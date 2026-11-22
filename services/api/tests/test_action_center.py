"""行动中心：把已有事实按"现在要做什么"聚合，不复制状态。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.crm import CrmSyncJob
from app.models.expense_reports import ExpenseReport, ExpenseReportItem
from app.models.proactive import ProactiveSuggestion
from app.models.reliability import BackgroundJob
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


def test_action_center_aggregates_existing_facts(client: TestClient) -> None:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"行动中心·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=4)).isoformat(),
            "window_end": (now + timedelta(days=7)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]
    trip_name = trip.json()["name"]
    job_ids: list[UUID] = []

    place = client.post(
        "/v1/places",
        headers=headers(),
        json={
            "name": f"行动中心地点·{uuid.uuid4().hex[:6]}",
            "city": "Berlin",
            "country_code": "DE",
            "lat": 52.52,
            "lon": 13.405,
        },
    )
    assert place.status_code == 201, place.text
    place_id = place.json()["id"]

    def visit_and_record(title: str) -> tuple[str, str]:
        visit = client.post(
            f"/v1/trips/{trip_id}/visits",
            headers=headers(),
            json={"place_id": place_id, "title": title, "service_minutes": 60},
        )
        assert visit.status_code == 201, visit.text
        record = client.post(
            f"/v1/visits/{visit.json()['id']}/records",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={
                "client_record_id": str(uuid.uuid4()),
                "outcome": "MET",
                "notes": title,
                "client_updated_at": now.isoformat(),
                "photos": [],
                "follow_up_tasks": [],
            },
        )
        assert record.status_code == 201, record.text
        return visit.json()["id"], record.json()["id"]

    visit_one, record_one = visit_and_record("CRM A")
    visit_two, record_two = visit_and_record("CRM B")

    with org_session(UUID(ORG_A)) as session:
        session.add(
            ProactiveSuggestion(
                organization_id=UUID(ORG_A),
                trip_id=UUID(trip_id),
                suggestion_type="VISA_RISK",
                status="PENDING",
                title="签证材料可能不足",
                rationale="距离出发不足 10 个工作日，材料清单仍有缺失。",
                evidence=[],
                proposed_action={"type": "CREATE_VISA_CASE"},
                confidence=0.8,
                requires_approval=True,
                severity="HIGH",
                dedupe_key=f"ac-{uuid.uuid4().hex}",
            )
        )
        session.add(
            ProactiveSuggestion(
                organization_id=UUID(ORG_A),
                trip_id=UUID(trip_id),
                suggestion_type="PLAN_READY",
                status="EXECUTED",
                title="候选计划已生成",
                rationale="已完成求解。",
                evidence=[],
                proposed_action={},
                confidence=0.9,
                requires_approval=False,
                severity="LOW",
                dedupe_key=f"ac-done-{uuid.uuid4().hex}",
                executed_at=now - timedelta(hours=1),
            )
        )
        report = ExpenseReport(
            organization_id=UUID(ORG_A),
            trip_id=UUID(trip_id),
            status="SUBMITTED",
            submitted_at=now,
        )
        session.add(report)
        session.flush()
        session.add_all(
            [
                ExpenseReportItem(
                    organization_id=UUID(ORG_A),
                    trip_id=UUID(trip_id),
                    report_id=report.id,
                    category="MEALS",
                    amount=Decimal("20.00"),
                    currency="EUR",
                    occurred_on=date.today(),
                ),
                ExpenseReportItem(
                    organization_id=UUID(ORG_A),
                    trip_id=UUID(trip_id),
                    report_id=report.id,
                    category="TRANSPORT",
                    amount=Decimal("50.00"),
                    currency="EUR",
                    occurred_on=date.today(),
                ),
            ]
        )
        running = BackgroundJob(
            organization_id=UUID(ORG_A),
            job_type="plan_trip",
            status="PENDING",
            payload={"trip_id": trip_id},
        )
        failed = BackgroundJob(
            organization_id=UUID(ORG_A),
            job_type="plan_trip",
            status="FAILED",
            attempts=3,
            last_error="求解器不可用",
            payload={"trip_id": trip_id},
        )
        session.add_all([running, failed])
        session.flush()
        job_ids.extend([running.id, failed.id])
        session.add_all(
            [
                CrmSyncJob(
                    organization_id=UUID(ORG_A),
                    trip_id=UUID(trip_id),
                    visit_id=UUID(visit_one),
                    visit_record_id=UUID(record_one),
                    status="PREPARED",
                    result={"detail": "待导入文件已生成", "external_id": "file:x"},
                ),
                CrmSyncJob(
                    organization_id=UUID(ORG_A),
                    trip_id=UUID(trip_id),
                    visit_id=UUID(visit_two),
                    visit_record_id=UUID(record_two),
                    status="FAILED",
                    error="CRM 供应商未配置",
                ),
            ]
        )

    try:
        feed = client.get("/v1/action-center", headers=headers())
        assert feed.status_code == 200, feed.text
        assert feed.json()["total"] >= 7
        scoped = client.get(
            "/v1/action-center", headers=headers(), params={"trip_id": trip_id}
        )
        assert scoped.status_code == 200
        body = scoped.json()
        assert body["total"] == 7
        assert body["counts"] == {
            "DECISION": 1,
            "APPROVAL": 1,
            "RUNNING": 1,
            "FAILED": 2,
            "PREPARATION": 1,
            "COMPLETED": 1,
        }
        assert [section["key"] for section in body["sections"]] == [
            "DECISION",
            "APPROVAL",
            "FAILED",
            "RUNNING",
            "PREPARATION",
            "COMPLETED",
        ]
        decision = body["sections"][0]["items"][0]
        assert decision["kind"] == "SUGGESTION"
        assert "APPROVE" in decision["available_actions"]
        assert decision["trip_name"] == trip_name
        approval = body["sections"][1]["items"][0]
        assert approval["kind"] == "EXPENSE_REPORT"
        assert "2 项费用" in approval["detail"]
        failed = body["sections"][2]["items"]
        assert {item["kind"] for item in failed} == {"JOB", "CRM_SYNC"}

        other_trip = client.get(
            "/v1/action-center",
            headers=headers(),
            params={"trip_id": "99999999-9999-9999-9999-999999999999"},
        )
        assert other_trip.status_code == 200
        assert other_trip.json()["total"] == 0

        other_org = client.get("/v1/action-center", headers=headers(ORG_B, USER_B))
        assert other_org.status_code == 200
        visible_trips = {
            item["trip_name"]
            for section in other_org.json()["sections"]
            for item in section["items"]
        }
        assert trip_name not in visible_trips
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(BackgroundJob).where(BackgroundJob.id.in_(job_ids)))
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
            from app.models.geo import Place

            session.execute(delete(Place).where(Place.id == UUID(place_id)))
