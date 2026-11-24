"""报销单闭环：快照 → 提交 → 审批/驳回。"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.identity import Membership, User
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

        cannot_cancel_submitted = client.post(
            f"/v1/expense-reports/{report['id']}:cancel",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert cannot_cancel_submitted.status_code == 409

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



def test_draft_report_can_be_cancelled_without_deleting_snapshot_or_expenses(
    client: TestClient,
) -> None:
    trip_id, expense_ids = setup(client)
    other_user_id = uuid.uuid4()
    try:
        with org_session(UUID(ORG_A)) as session:
            session.add(
                User(
                    id=other_user_id,
                    email=f"{other_user_id}@example.test",
                    display_name="Other traveler",
                )
            )
            session.flush()
            session.add(
                Membership(
                    organization_id=UUID(ORG_A),
                    user_id=other_user_id,
                    role="TRAVELER",
                )
            )

        created = client.post(
            f"/v1/trips/{trip_id}/expense-reports",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={},
        )
        assert created.status_code == 201, created.text
        report_id = created.json()["id"]
        assert len(created.json()["items"]) == 2

        other_reports = client.get(
            f"/v1/trips/{trip_id}/expense-reports",
            headers=headers(str(other_user_id)),
        )
        assert other_reports.status_code == 200, other_reports.text
        other_report = next(row for row in other_reports.json() if row["id"] == report_id)
        assert other_report["can_cancel"] is False

        owner_reports = client.get(
            f"/v1/trips/{trip_id}/expense-reports", headers=headers()
        )
        assert owner_reports.status_code == 200, owner_reports.text
        owner_report = next(row for row in owner_reports.json() if row["id"] == report_id)
        assert owner_report["can_cancel"] is True

        denied = client.post(
            f"/v1/expense-reports/{report_id}:cancel",
            headers={
                **headers(str(other_user_id)),
                "Idempotency-Key": str(uuid.uuid4()),
            },
        )
        assert denied.status_code == 403, denied.text

        key = str(uuid.uuid4())
        cancelled = client.post(
            f"/v1/expense-reports/{report_id}:cancel",
            headers={**headers(), "Idempotency-Key": key},
        )
        assert cancelled.status_code == 200, cancelled.text
        body = cancelled.json()
        assert body["status"] == "CANCELLED"
        assert body["cancelled_at"] is not None
        assert body["cancelled_by_user_id"] == USER_A
        assert body["cancellation_reason"] == "USER_CANCELLED_DRAFT"
        assert body["can_cancel"] is False
        assert len(body["items"]) == 2

        # The idempotency key replays the same response; a new key is also a safe
        # no-op because CANCELLED is already a terminal state.
        replay = client.post(
            f"/v1/expense-reports/{report_id}:cancel",
            headers={**headers(), "Idempotency-Key": key},
        )
        assert replay.status_code == 200
        assert replay.json()["status"] == "CANCELLED"
        repeated = client.post(
            f"/v1/expense-reports/{report_id}:cancel",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert repeated.status_code == 200
        assert repeated.json()["id"] == report_id

        detail = client.get(f"/v1/expense-reports/{report_id}", headers=headers())
        assert detail.status_code == 200
        assert len(detail.json()["items"]) == 2
        remaining = client.get(f"/v1/trips/{trip_id}/expenses", headers=headers())
        assert remaining.status_code == 200
        assert {item["id"] for item in remaining.json()} == set(expense_ids)

        blocked_submit = client.post(
            f"/v1/expense-reports/{report_id}:submit",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert blocked_submit.status_code == 409

        # Cancelled snapshots are not eligible for the server-side coverage union.
        first_partial = client.post(
            f"/v1/trips/{trip_id}/expense-reports",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={"expense_ids": [expense_ids[0]]},
        )
        assert first_partial.status_code == 201, first_partial.text
        first_partial_id = first_partial.json()["id"]
        uncovered = client.post(
            f"/v1/expense-reports/{first_partial_id}:submit",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert uncovered.status_code == 409, uncovered.text
        assert set(uncovered.json()["error"]["details"]["missing_expense_ids"]) == {
            expense_ids[1]
        }

        second_partial = client.post(
            f"/v1/trips/{trip_id}/expense-reports",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={"expense_ids": [expense_ids[1]]},
        )
        assert second_partial.status_code == 201, second_partial.text
        first_submitted = client.post(
            f"/v1/expense-reports/{first_partial_id}:submit",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert first_submitted.status_code == 200, first_submitted.text
        second_submitted = client.post(
            f"/v1/expense-reports/{second_partial.json()['id']}:submit",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert second_submitted.status_code == 200, second_submitted.text

        audit = client.get(f"/v1/trips/{trip_id}/audit", headers=headers())
        assert audit.status_code == 200, audit.text
        cancel_event = next(
            event
            for event in audit.json()
            if event["kind"] == "expense_report" and event["action"] == "CANCELLED"
        )
        assert cancel_event["actor"] == USER_A
        assert cancel_event["detail"]["report_id"] == report_id
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(Membership).where(
                    Membership.organization_id == UUID(ORG_A),
                    Membership.user_id == other_user_id,
                )
            )
            session.execute(delete(User).where(User.id == other_user_id))
        cleanup(trip_id)


def test_report_submission_requires_trip_wide_snapshot_coverage(client: TestClient) -> None:
    trip_id, expense_ids = setup(client)
    try:
        first_half = client.post(
            f"/v1/trips/{trip_id}/expense-reports",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={"expense_ids": [expense_ids[0]]},
        )
        assert first_half.status_code == 201, first_half.text
        first_report_id = first_half.json()["id"]

        incomplete = client.post(
            f"/v1/expense-reports/{first_report_id}:submit",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert incomplete.status_code == 409, incomplete.text
        assert set(incomplete.json()["error"]["details"]["missing_expense_ids"]) == {expense_ids[1]}

        second_half = client.post(
            f"/v1/trips/{trip_id}/expense-reports",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={"expense_ids": [expense_ids[1]]},
        )
        assert second_half.status_code == 201, second_half.text

        second_report_id = second_half.json()["id"]

        # A rejected report no longer contributes to Trip-wide snapshot coverage.
        submitted_second = client.post(
            f"/v1/expense-reports/{second_report_id}:submit",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert submitted_second.status_code == 200, submitted_second.text
        rejected_second = client.post(
            f"/v1/expense-reports/{second_report_id}:reject",
            headers={**headers(USER_ADMIN), "Idempotency-Key": str(uuid.uuid4())},
            json={"note": "补充票据后重新提交"},
        )
        assert rejected_second.status_code == 200, rejected_second.text

        invalidated = client.post(
            f"/v1/expense-reports/{first_report_id}:submit",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert invalidated.status_code == 409, invalidated.text
        assert set(invalidated.json()["error"]["details"]["missing_expense_ids"]) == {expense_ids[1]}

        replacement = client.post(
            f"/v1/trips/{trip_id}/expense-reports",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={"expense_ids": [expense_ids[1]]},
        )
        assert replacement.status_code == 201, replacement.text

        # Multiple non-rejected snapshots may collectively cover a Trip's expenses.
        submitted_first = client.post(
            f"/v1/expense-reports/{first_report_id}:submit",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert submitted_first.status_code == 200, submitted_first.text
        assert submitted_first.json()["status"] == "SUBMITTED"

        submitted_replacement = client.post(
            f"/v1/expense-reports/{replacement.json()['id']}:submit",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
        )
        assert submitted_replacement.status_code == 200, submitted_replacement.text
        assert submitted_replacement.json()["status"] == "SUBMITTED"
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
