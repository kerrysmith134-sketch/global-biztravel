"""H2.13 closeout readiness API and Action Center integration tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.expense_reports import ExpenseReport
from app.models.proactive import Approval
from app.models.records import VisitFollowUp, VisitRecord
from app.models.trip import Place, Trip, Visit
from app.models.workflow import WorkflowInstance
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
USER_A = "22222222-2222-2222-2222-222222222222"
USER_B = "44444444-4444-4444-4444-444444444444"
USER_ADMIN = "55555555-5555-5555-5555-555555555555"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(
    org: str = ORG_A, user: str = USER_A, key: str | None = None
) -> dict[str, str]:
    result = {"X-Org-Id": org, "X-User-Id": user}
    if key:
        result["Idempotency-Key"] = key
    return result


def make_trip(client: TestClient) -> str:
    now = datetime.now(UTC)
    response = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"结账评估·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=2)).isoformat(),
            "window_end": (now + timedelta(days=5)).isoformat(),
            "countries": ["DE"],
            "currency": "EUR",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def make_place(client: TestClient) -> str:
    response = client.post(
        "/v1/places",
        headers=headers(),
        json={
            "name": f"Closeout location {uuid.uuid4().hex[:6]}",
            "city": "Berlin",
            "country_code": "DE",
            "lat": 52.52,
            "lon": 13.405,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def make_visit(client: TestClient, trip_id: str, place_id: str, title: str) -> str:
    response = client.post(
        f"/v1/trips/{trip_id}/visits",
        headers=headers(key=str(uuid.uuid4())),
        json={"place_id": place_id, "title": title, "service_minutes": 60},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def cleanup(trip_id: str, place_id: str | None = None) -> None:
    with org_session(UUID(ORG_A)) as session:
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
        if place_id is not None:
            session.execute(delete(Place).where(Place.id == UUID(place_id)))


def test_closeout_blocks_open_visits_missing_outcomes_and_unreported_expenses(
    client: TestClient,
) -> None:
    trip_id = make_trip(client)
    place_id = make_place(client)
    make_visit(client, trip_id, place_id, "Planned visit")
    done_visit_id = make_visit(client, trip_id, place_id, "Done without record")
    with org_session(UUID(ORG_A)) as session:
        done = session.get(Visit, UUID(done_visit_id))
        assert done is not None
        done.status = "DONE"

    expense = client.post(
        f"/v1/trips/{trip_id}/expenses",
        headers=headers(key=str(uuid.uuid4())),
        json={
            "category": "MEALS",
            "amount": "34.20",
            "currency": "EUR",
            "occurred_on": datetime.now(UTC).date().isoformat(),
        },
    )
    assert expense.status_code == 201, expense.text
    before = client.get(f"/v1/trips/{trip_id}", headers=headers()).json()["version"]
    try:
        response = client.get(
            f"/v1/trips/{trip_id}/closeout-readiness", headers=headers()
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "BLOCKED"
        codes = {issue["code"] for issue in body["blockers"]}
        assert "VISIT_NOT_CLOSED" in codes
        assert "VISIT_OUTCOME_MISSING" in codes
        assert "EXPENSE_NOT_IN_REPORT" in codes
        assert body["summary"]["blocker_count"] == 3
        assert body["summary"]["open_business_follow_up_count"] == 0
        assert body["totals_by_currency"] == [
            {"currency": "EUR", "amount": 34.2, "count": 1}
        ]
        after = client.get(f"/v1/trips/{trip_id}", headers=headers()).json()["version"]
        assert before == after
        stolen = client.get(
            f"/v1/trips/{trip_id}/closeout-readiness",
            headers=headers(ORG_B, USER_B),
        )
        assert stolen.status_code == 404
    finally:
        cleanup(trip_id, place_id)


def test_business_follow_up_is_warning_not_financial_blocker(client: TestClient) -> None:
    trip_id = make_trip(client)
    place_id = make_place(client)
    visit_id = make_visit(client, trip_id, place_id, "Customer follow-up")
    with org_session(UUID(ORG_A)) as session:
        visit = session.get(Visit, UUID(visit_id))
        assert visit is not None
        visit.status = "DONE"
        session.add(
            VisitRecord(
                organization_id=UUID(ORG_A),
                visit_id=UUID(visit_id),
                client_record_id=f"closeout-{uuid.uuid4()}",
                outcome="MET",
                notes="Meeting completed.",
                client_updated_at=datetime.now(UTC),
                applied=True,
            )
        )
        session.add(
            VisitFollowUp(
                organization_id=UUID(ORG_A),
                trip_id=UUID(trip_id),
                visit_id=UUID(visit_id),
                source_type="MANUAL",
                source_key="MANUAL:closeout-test",
                title="Send revised proposal",
                status="OPEN",
            )
        )
    try:
        response = client.get(
            f"/v1/trips/{trip_id}/closeout-readiness", headers=headers()
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["blockers"] == []
        assert body["status"] == "READY_WITH_EXCEPTIONS"
        assert body["summary"]["open_business_follow_up_count"] == 1
        assert any(
            issue["code"] == "BUSINESS_FOLLOW_UP_OPEN"
            for issue in body["warnings"]
        )
    finally:
        cleanup(trip_id, place_id)


def test_closeout_separates_currencies_and_flags_latest_rejected_report(
    client: TestClient,
) -> None:
    trip_id = make_trip(client)
    try:
        for amount, currency in (("10.00", "EUR"), ("20.00", "USD")):
            created = client.post(
                f"/v1/trips/{trip_id}/expenses",
                headers=headers(key=str(uuid.uuid4())),
                json={
                    "category": "TRANSPORT",
                    "amount": amount,
                    "currency": currency,
                    "occurred_on": datetime.now(UTC).date().isoformat(),
                },
            )
            assert created.status_code == 201, created.text
        report = client.post(
            f"/v1/trips/{trip_id}/expense-reports",
            headers=headers(key=str(uuid.uuid4())),
            json={},
        )
        assert report.status_code == 201, report.text
        report_id = report.json()["id"]
        submitted = client.post(
            f"/v1/expense-reports/{report_id}:submit",
            headers=headers(key=str(uuid.uuid4())),
        )
        assert submitted.status_code == 200, submitted.text
        rejected = client.post(
            f"/v1/expense-reports/{report_id}:reject",
            headers=headers(USER_ADMIN, ORG_A, str(uuid.uuid4())),
            json={"note": "Receipt evidence needs correction"},
        )
        assert rejected.status_code == 200, rejected.text

        response = client.get(
            f"/v1/trips/{trip_id}/closeout-readiness", headers=headers()
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "BLOCKED"
        assert any(
            item["code"] == "LATEST_REPORT_REJECTED" for item in body["blockers"]
        )
        assert any(
            item["code"] == "MULTI_CURRENCY_REVIEW" for item in body["warnings"]
        )
        assert {item["currency"] for item in body["totals_by_currency"]} == {
            "EUR",
            "USD",
        }
    finally:
        cleanup(trip_id)


def test_action_center_surfaces_only_hard_closeout_blockers(client: TestClient) -> None:
    trip_id = make_trip(client)
    place_id = make_place(client)
    make_visit(client, trip_id, place_id, "Visit needing closeout")
    with org_session(UUID(ORG_A)) as session:
        trip = session.get(Trip, UUID(trip_id))
        assert trip is not None
        trip.status = "ACTIVE"
    try:
        response = client.get(
            "/v1/action-center",
            headers=headers(),
            params={"trip_id": trip_id},
        )
        assert response.status_code == 200, response.text
        items = [
            item
            for section in response.json()["sections"]
            for item in section["items"]
        ]
        closeout_items = [item for item in items if item["kind"] == "TRIP_CLOSEOUT"]
        assert any(item["title"] == "拜访尚未完成" for item in closeout_items)
        assert all(item["severity"] == "HIGH" for item in closeout_items)
    finally:
        cleanup(trip_id, place_id)
