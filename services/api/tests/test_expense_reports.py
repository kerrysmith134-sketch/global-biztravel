"""报销单闭环：快照 → 提交 → 审批/驳回。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.proactive import Approval
from app.models.trip import Trip
from app.models.workflow import WorkflowInstance
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
USER_A = "22222222-2222-2222-2222-222222222222"
USER_ADMIN = "55555555-5555-5555-5555-555555555555"
USER_B = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(user: str = USER_A, org: str = ORG_A) -> dict[str, str]:
    return {"X-Org-Id": org, "X-User-Id": user}


def setup(client: TestClient) -> tuple[str, list[str]]:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"报销单·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=3)).isoformat(),
            "window_end": (now + timedelta(days=5)).isoformat(),
            "countries": ["DE"],
            "currency": "EUR",
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]
    ids = []
    for amount, currency in (("120.00", "EUR"), ("80.00", "USD")):
        resp = client.post(
            f"/v1/trips/{trip_id}/expenses",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={
                "category": "MEALS",
                "amount": amount,
                "currency": currency,
                "occurred_on": date.today().isoformat(),
            },
        )
        assert resp.status_code == 201, resp.text
        ids.append(resp.json()["id"])
    return trip_id, ids


def cleanup(trip_id: str) -> None:
    with org_session(UUID(ORG_A)) as session:
        from app.models.expense_reports import ExpenseReport

        report_ids = list(
            session.scalars(
                select(ExpenseReport.id).where(ExpenseReport.trip_id == UUID(trip_id))
            )
        )
        if report_ids:
            session.execute(
                delete(Approval).where(
                    Approval.subject_type == "expense_report",
                    Approval.subject_id.in_(report_ids),
                )
            )
        session.execute(
            delete(WorkflowInstance).where(WorkflowInstance.trip_id == UUID(trip_id))
        )
        session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))


def test_report_snapshot_submit_and_approve(client: TestClient) -> None:
    trip_id, expense_ids = setup(client)
    try:
        created = client.post(
            f"/v1/trips/{trip_id}/expense-reports",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={"notes": "Q4 出差报销", "expense_ids": expense_ids},
        )
        assert created.status_code == 201, created.text
        report = created.json()
        assert report["status"] == "DRAFT"
        assert len(report["items"]) == 2
        totals = {item["currency"]: item["amount"] for item in report["totals"]}
        assert totals == {"EUR": 120.0, "USD": 80.0}

        submitted = client.post(
            f"/v1/expense-reports/{report['id']}:submit",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert submitted.status_code == 200, submitted.text
        assert submitted.json()["status"] == "SUBMITTED"
        assert submitted.json()["submitted_at"] is not None

        approved = client.post(
            f"/v1/expense-reports/{report['id']}:approve",
            headers={**headers(USER_ADMIN), "Idempotency-Key": str(uuid.uuid4())},
            json={"note": "同意"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "APPROVED"
        assert approved.json()["decided_by_user_id"] == USER_ADMIN

        with org_session(UUID(ORG_A)) as session:
            approval = session.scalars(
                select(Approval).where(
                    Approval.subject_type == "expense_report",
                    Approval.subject_id == UUID(report["id"]),
                )
            ).one()
            assert approval.decision == "APPROVED"
            workflow = session.scalars(
                select(WorkflowInstance).where(
                    WorkflowInstance.workflow_type == "EXPENSE_REPORT",
                    WorkflowInstance.payload["report_id"].astext == report["id"],
                )
            ).one()
            assert workflow.status == "COMPLETED"

        again = client.post(
            f"/v1/expense-reports/{report['id']}:submit",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert again.status_code == 409

        stolen = client.get(
            f"/v1/expense-reports/{report['id']}", headers=headers(USER_B, ORG_B)
        )
        assert stolen.status_code == 404
    finally:
        cleanup(trip_id)


def test_report_snapshot_survives_expense_deletion_and_reject(client: TestClient) -> None:
    trip_id, expense_ids = setup(client)
    try:
        created = client.post(
            f"/v1/trips/{trip_id}/expense-reports",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={},
        )
        assert created.status_code == 201, created.text
        report_id = created.json()["id"]

        removed = client.delete(f"/v1/expenses/{expense_ids[0]}", headers=headers())
        assert removed.status_code == 204
        detail = client.get(f"/v1/expense-reports/{report_id}", headers=headers())
        assert detail.status_code == 200
        assert len(detail.json()["items"]) == 2  # 快照不受源费用删除影响

        submitted = client.post(
            f"/v1/expense-reports/{report_id}:submit",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert submitted.status_code == 200

        rejected = client.post(
            f"/v1/expense-reports/{report_id}:reject",
            headers={**headers(USER_ADMIN), "Idempotency-Key": str(uuid.uuid4())},
            json={"note": "缺少票据"},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "REJECTED"
    finally:
        cleanup(trip_id)
