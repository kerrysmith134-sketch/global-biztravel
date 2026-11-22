"""PostgreSQL acceptance tests for market opportunities and Workspace/Trip links."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.market import (
    MarketActivityEvent,
    MarketOpportunity,
    MarketOpportunityAssessment,
    MarketOpportunityVisitLink,
    MarketWorkspace,
    MarketWorkspaceTripLink,
)
from app.models.geo import Place
from app.models.trip import Trip, Visit
from app.models.workflow import Evidence
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
ADMIN_A = "55555555-5555-5555-5555-555555555555"
USER_B = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(key: str | None = None, *, org: str = ORG_A, user: str = ADMIN_A) -> dict[str, str]:
    return {
        "X-Org-Id": org,
        "X-User-Id": user,
        "Idempotency-Key": key or str(uuid.uuid4()),
    }


def create_workspace(client: TestClient, label: str) -> str:
    response = client.post(
        "/v1/market/workspaces",
        headers=headers(),
        json={"name": f"{label} {uuid.uuid4().hex[:8]}", "objective": "验证机会到差旅行动闭环"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_evidence(client: TestClient, workspace_id: str, url: str, content: str) -> dict:
    response = client.post(
        f"/v1/market/workspaces/{workspace_id}/evidence",
        headers=headers(),
        json={"canonical_ref": url, "content": content, "kind": "WEB_SOURCE"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_trip(org_id: str, user_id: str, name: str) -> str:
    start = datetime.now(UTC) + timedelta(days=30)
    with org_session(UUID(org_id)) as session:
        trip = Trip(
            organization_id=UUID(org_id),
            name=name,
            purpose="Market research field action",
            owner_user_id=UUID(user_id),
            home_timezone="Africa/Nairobi",
            currency="KES",
            window_start=start,
            window_end=start + timedelta(days=5),
            countries=["KE"],
            status="DRAFT",
            version=1,
        )
        session.add(trip)
        session.flush()
        return str(trip.id)


def create_visit(trip_id: str, *, title: str = "Buyer procurement meeting") -> tuple[str, str]:
    with org_session(UUID(ORG_A)) as session:
        place = Place(
            organization_id=UUID(ORG_A),
            name="Nairobi buyer office",
            city="Nairobi",
            country_code="KE",
            timezone="Africa/Nairobi",
            lat=-1.2864,
            lon=36.8172,
            geocode_status="RESOLVED",
        )
        session.add(place)
        session.flush()
        visit = Visit(
            organization_id=UUID(ORG_A),
            trip_id=UUID(trip_id),
            place_id=place.id,
            title=title,
            objective="Discuss buyer requirements and next steps.",
            service_minutes=60,
            priority=1,
            business_value=125000,
            movement_mode="GROUND",
            status="PLANNED",
        )
        session.add(visit)
        session.flush()
        return str(visit.id), str(place.id)


def cleanup(
    workspace_ids: list[str], trip_ids: list[str], place_ids: list[str] | None = None
) -> None:
    with org_session(UUID(ORG_A)) as session:
        if workspace_ids:
            workspace_uuids = [UUID(item) for item in workspace_ids]
            # Delete Workspace first: cascading deletes Opportunity/Insight evidence links.
            # Then remove legacy polymorphic Evidence rows, which have no Workspace FK.
            session.execute(
                delete(MarketWorkspace).where(
                    MarketWorkspace.organization_id == UUID(ORG_A),
                    MarketWorkspace.id.in_(workspace_uuids),
                )
            )
            session.execute(
                delete(Evidence).where(
                    Evidence.organization_id == UUID(ORG_A),
                    Evidence.subject_type == "market_workspace",
                    Evidence.subject_id.in_(workspace_uuids),
                )
            )
        if trip_ids:
            session.execute(
                delete(Trip).where(
                    Trip.organization_id == UUID(ORG_A),
                    Trip.id.in_([UUID(item) for item in trip_ids]),
                )
            )
        if place_ids:
            session.execute(
                delete(Place).where(
                    Place.organization_id == UUID(ORG_A),
                    Place.id.in_([UUID(item) for item in place_ids]),
                )
            )
    if trip_ids:
        # Cross-tenant test trips are created in ORG_B and cleaned independently.
        with org_session(UUID(ORG_B)) as session:
            session.execute(
                delete(Trip).where(
                    Trip.organization_id == UUID(ORG_B),
                    Trip.id.in_([UUID(item) for item in trip_ids]),
                )
            )


def test_opportunity_evidence_assessment_qualification_and_outcome(client: TestClient) -> None:
    workspace_id: str | None = None
    try:
        workspace_id = create_workspace(client, "Opportunity qualification")
        evidence = create_evidence(
            client,
            workspace_id,
            "https://example.com/procurement-signal",
            "Two buyers published specific procurement plans for the next budget cycle.",
        )
        created = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities",
            headers=headers(),
            json={
                "title": "Emerging procurement demand",
                "description": "A candidate opportunity inferred from public buyer signals.",
                "next_action": "Validate buyer authority and timing.",
            },
        )
        assert created.status_code == 201, created.text
        opportunity_id = created.json()["id"]
        assert created.json()["status"] == "DISCOVERED"
        assert created.json()["assessment_version"] == 0

        candidate = client.post(
            f"/v1/market/workspaces/{workspace_id}/entities",
            headers=headers(),
            json={
                "entity_kind": "organization",
                "display_name": "Example Buyer Cooperative",
                "country_code": "KE",
                "canonical_domain": "buyer.example",
                "discovery_reason": "Named in the procurement source.",
            },
        )
        assert candidate.status_code == 201, candidate.text
        entity_id = candidate.json()["entity"]["id"]
        entity_link = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/entities",
            headers=headers(),
            json={"entity_ids": [entity_id], "relationship_note": "Primary buyer organization."},
        )
        assert entity_link.status_code == 201, entity_link.text
        assert entity_link.json()[0]["id"] == entity_id

        linked = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/evidence",
            headers=headers(),
            json={"evidence_id": evidence["id"], "relation_type": "SUPPORTS"},
        )
        assert linked.status_code == 201, linked.text
        assert linked.json()["relation_type"] == "SUPPORTS"

        first_assessment = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:assess",
            headers=headers(),
            json={
                "confidence_level": "MEDIUM",
                "summary": "Buyer intent is specific, but decision authority remains unknown.",
                "dimensions": {
                    "Demand Evidence": {
                        "value": "MEDIUM",
                        "rationale": "Two independent procurement plans have been captured.",
                        "evidence_refs": [evidence["id"]],
                        "unknowns": ["Final budget approval"],
                    },
                    "Commercial Potential": {
                        "value": "UNKNOWN",
                        "rationale": "No reliable deal-size evidence has been captured.",
                        "evidence_refs": [],
                        "unknowns": ["Deal size", "Procurement schedule"],
                    },
                },
            },
        )
        assert first_assessment.status_code == 201, first_assessment.text
        assert first_assessment.json()["version"] == 1
        assert first_assessment.json()["dimensions"]["Commercial Potential"]["value"] == "UNKNOWN"

        # An assessment may only cite evidence explicitly linked to this Opportunity.
        other_evidence = create_evidence(
            client,
            workspace_id,
            "https://example.com/unlinked-market-note",
            "A general market note not yet attached to this opportunity.",
        )
        invalid_assessment = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:assess",
            headers=headers(),
            json={
                "dimensions": {
                    "Demand": {
                        "value": "HIGH",
                        "rationale": "Not linked evidence must not be cited.",
                        "evidence_refs": [other_evidence["id"]],
                    }
                }
            },
        )
        assert invalid_assessment.status_code == 409, invalid_assessment.text

        second_assessment = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:assess",
            headers=headers(),
            json={
                "confidence_level": "LOW",
                "summary": "The market signal remains early.",
                "dimensions": {
                    "Demand Evidence": {
                        "value": "LOW",
                        "rationale": "Evidence coverage is still narrow.",
                        "evidence_refs": [evidence["id"]],
                        "unknowns": ["Buyer budget"],
                    }
                },
            },
        )
        assert second_assessment.status_code == 201, second_assessment.text
        assert second_assessment.json()["version"] == 2

        def transition(target: str) -> dict:
            response = client.post(
                f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:transition",
                headers=headers(),
                json={"target_status": target},
            )
            assert response.status_code == 200, response.text
            return response.json()

        assert transition("RESEARCHING")["status"] == "RESEARCHING"
        assert transition("EVIDENCE_READY")["status"] == "EVIDENCE_READY"
        assert transition("QUALIFICATION_PENDING")["status"] == "QUALIFICATION_PENDING"

        qualified = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:qualify",
            headers=headers(),
            json={"qualification_reason": "Supporting evidence has been reviewed by the owner."},
        )
        assert qualified.status_code == 200, qualified.text
        assert qualified.json()["status"] == "QUALIFIED"
        assert qualified.json()["qualified_by_user_id"] == ADMIN_A
        assert qualified.json()["qualified_at"] is not None

        action = transition("ACTION_PLANNED")
        assert action["status"] == "ACTION_PLANNED"
        outcome = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:outcome",
            headers=headers(),
            json={"outcome": "WON", "outcome_note": "Buyer signed the purchase agreement."},
        )
        assert outcome.status_code == 200, outcome.text
        assert outcome.json()["status"] == "WON"
        assert outcome.json()["outcome_recorded_by_user_id"] == ADMIN_A

        detail = client.get(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}",
            headers=headers(),
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["opportunity"]["assessment_version"] == 2
        assert len(detail.json()["assessments"]) == 2
        assert [item["version"] for item in detail.json()["assessments"]] == [1, 2]
        assert len(detail.json()["evidence"]) == 1
        assert [item["id"] for item in detail.json()["entities"]] == [entity_id]

        with org_session(UUID(ORG_A)) as session:
            stored = session.get(MarketOpportunity, UUID(opportunity_id))
            assert stored is not None and stored.status == "WON"
            assert session.scalar(
                select(func.count()).select_from(MarketActivityEvent).where(
                    MarketActivityEvent.organization_id == UUID(ORG_A),
                    MarketActivityEvent.workspace_id == UUID(workspace_id),
                    MarketActivityEvent.aggregate_id == UUID(opportunity_id),
                )
            ) >= 6
            versions = session.scalars(
                select(MarketOpportunityAssessment)
                .where(MarketOpportunityAssessment.opportunity_id == UUID(opportunity_id))
                .order_by(MarketOpportunityAssessment.version)
            ).all()
            assert [row.version for row in versions] == [1, 2]
    finally:
        cleanup([workspace_id] if workspace_id else [], [])


def test_opportunity_contradiction_blocks_qualification(client: TestClient) -> None:
    workspace_id: str | None = None
    try:
        workspace_id = create_workspace(client, "Opportunity conflict")
        support = create_evidence(
            client, workspace_id, "https://example.com/buyer-intent", "Buyer has a signed procurement request."
        )
        contradiction = create_evidence(
            client, workspace_id, "https://example.org/budget-freeze", "Buyer publicly paused this category's budget."
        )
        created = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities",
            headers=headers(),
            json={"title": "Conflicting buyer opportunity signal"},
        )
        assert created.status_code == 201, created.text
        opportunity_id = created.json()["id"]
        for item, relation in ((support, "SUPPORTS"), (contradiction, "CONTRADICTS")):
            response = client.post(
                f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/evidence",
                headers=headers(),
                json={"evidence_id": item["id"], "relation_type": relation},
            )
            assert response.status_code == 201, response.text

        for target in ("RESEARCHING", "EVIDENCE_READY", "QUALIFICATION_PENDING"):
            response = client.post(
                f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:transition",
                headers=headers(),
                json={"target_status": target},
            )
            assert response.status_code == 200, response.text
        blocked = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:qualify",
            headers=headers(),
            json={"qualification_reason": "This should be blocked by contradictory evidence."},
        )
        assert blocked.status_code == 409, blocked.text
        assert client.get(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}",
            headers=headers(),
        ).json()["opportunity"]["status"] == "QUALIFICATION_PENDING"
    finally:
        cleanup([workspace_id] if workspace_id else [], [])


def test_workspace_trip_link_is_reversible_and_keeps_trip(client: TestClient) -> None:
    workspace_id: str | None = None
    trip_id: str | None = None
    try:
        workspace_id = create_workspace(client, "Workspace trip relation")
        trip_id = create_trip(ORG_A, ADMIN_A, "Market research field trip")
        payload = {"trip_id": trip_id, "link_note": "Visit prospects identified in this research."}
        key = str(uuid.uuid4())
        linked = client.post(
            f"/v1/market/workspaces/{workspace_id}/trips",
            headers=headers(key=key),
            json=payload,
        )
        assert linked.status_code == 201, linked.text
        assert linked.json()["link_status"] == "LINKED"
        assert linked.json()["trip_name"] == "Market research field trip"

        replay = client.post(
            f"/v1/market/workspaces/{workspace_id}/trips",
            headers=headers(key=key),
            json=payload,
        )
        assert replay.status_code == 201, replay.text
        assert replay.json()["id"] == linked.json()["id"]

        unlinked = client.delete(
            f"/v1/market/workspaces/{workspace_id}/trips/{trip_id}",
            headers=headers(),
        )
        assert unlinked.status_code == 200, unlinked.text
        assert unlinked.json()["link_status"] == "UNLINKED"
        assert unlinked.json()["trip_name"] == "Market research field trip"

        history = client.get(
            f"/v1/market/workspaces/{workspace_id}/trips?include_unlinked=true",
            headers=headers(),
        )
        assert history.status_code == 200, history.text
        assert len(history.json()) == 1
        assert history.json()[0]["link_status"] == "UNLINKED"

        relinked = client.post(
            f"/v1/market/workspaces/{workspace_id}/trips",
            headers=headers(),
            json=payload,
        )
        assert relinked.status_code == 201, relinked.text
        assert relinked.json()["id"] == linked.json()["id"]
        assert relinked.json()["link_status"] == "LINKED"

        # Workspace deletion removes only the relation; it must not delete the actual Trip.
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(MarketWorkspace).where(
                    MarketWorkspace.organization_id == UUID(ORG_A),
                    MarketWorkspace.id == UUID(workspace_id),
                )
            )
        with org_session(UUID(ORG_A)) as session:
            assert session.get(Trip, UUID(trip_id)) is not None
            assert session.scalar(
                select(func.count()).select_from(MarketWorkspaceTripLink).where(
                    MarketWorkspaceTripLink.organization_id == UUID(ORG_A),
                    MarketWorkspaceTripLink.workspace_id == UUID(workspace_id),
                )
            ) == 0
    finally:
        cleanup([workspace_id] if workspace_id else [], [trip_id] if trip_id else [])


def test_opportunity_execution_links_trip_visits_with_reversible_audit(client: TestClient) -> None:
    workspace_id: str | None = None
    trip_id: str | None = None
    visit_id: str | None = None
    place_id: str | None = None
    try:
        workspace_id = create_workspace(client, "Opportunity execution")
        trip_id = create_trip(ORG_A, ADMIN_A, "East Africa customer visits")
        visit_id, place_id = create_visit(trip_id)
        opportunity_response = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities",
            headers=headers(),
            json={
                "title": "Verified buyer procurement opportunity",
                "description": "A user-reviewed market opportunity.",
                "next_action": "Arrange an in-person buyer meeting.",
            },
        )
        assert opportunity_response.status_code == 201, opportunity_response.text
        opportunity_id = opportunity_response.json()["id"]

        # A Trip must belong to the Workspace before its visits can be offered as candidates.
        trip_link = client.post(
            f"/v1/market/workspaces/{workspace_id}/trips",
            headers=headers(),
            json={"trip_id": trip_id, "link_note": "Field execution for this research."},
        )
        assert trip_link.status_code == 201, trip_link.text

        before_qualification = client.get(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/execution",
            headers=headers(),
        )
        assert before_qualification.status_code == 200, before_qualification.text
        assert before_qualification.json()["can_link_visits"] is False
        assert "ACTION_PLANNED" in before_qualification.json()["blocked_reason"]
        assert [item["visit_id"] for item in before_qualification.json()["candidates"]] == [visit_id]

        premature = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/visits",
            headers=headers(),
            json={"trip_id": trip_id, "visit_id": visit_id},
        )
        assert premature.status_code == 409, premature.text

        evidence = create_evidence(
            client,
            workspace_id,
            "https://buyer.example.test/procurement-plan",
            "The buyer confirms an active supplier procurement programme.",
        )
        evidence_link = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/evidence",
            headers=headers(),
            json={"evidence_id": evidence["id"], "relation_type": "SUPPORTS"},
        )
        assert evidence_link.status_code == 201, evidence_link.text

        for target in ("RESEARCHING", "EVIDENCE_READY", "QUALIFICATION_PENDING"):
            transitioned = client.post(
                f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:transition",
                headers=headers(),
                json={"target_status": target},
            )
            assert transitioned.status_code == 200, transitioned.text
        qualified = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:qualify",
            headers=headers(),
            json={"qualification_reason": "The evidence and buyer identity were reviewed."},
        )
        assert qualified.status_code == 200, qualified.text
        planned = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}:transition",
            headers=headers(),
            json={"target_status": "ACTION_PLANNED"},
        )
        assert planned.status_code == 200, planned.text

        execution = client.get(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/execution",
            headers=headers(),
        )
        assert execution.status_code == 200, execution.text
        assert execution.json()["can_link_visits"] is True
        assert execution.json()["blocked_reason"] is None
        assert execution.json()["candidates"][0]["customer_id"] is None
        assert execution.json()["candidates"][0]["place_name"] == "Nairobi buyer office"

        with org_session(UUID(ORG_A)) as session:
            trip_version_before = session.get(Trip, UUID(trip_id)).version

        payload = {
            "trip_id": trip_id,
            "visit_id": visit_id,
            "link_note": "Buyer meeting scheduled to validate procurement timing.",
        }
        key = str(uuid.uuid4())
        linked = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/visits",
            headers=headers(key=key),
            json=payload,
        )
        assert linked.status_code == 201, linked.text
        link_id = linked.json()["id"]
        assert linked.json()["link_status"] == "LINKED"
        assert linked.json()["visit_status"] == "PLANNED"
        assert linked.json()["trip_name"] == "East Africa customer visits"
        assert linked.json()["linked_by_user_id"] == ADMIN_A

        replay = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/visits",
            headers=headers(key=key),
            json=payload,
        )
        assert replay.status_code == 201, replay.text
        assert replay.json()["id"] == link_id

        # A semantic duplicate under a new idempotency key reuses the same link row.
        duplicate = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/visits",
            headers=headers(),
            json=payload,
        )
        assert duplicate.status_code == 201, duplicate.text
        assert duplicate.json()["id"] == link_id

        after_link = client.get(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/execution",
            headers=headers(),
        )
        assert after_link.status_code == 200, after_link.text
        assert after_link.json()["candidates"] == []
        assert len(after_link.json()["links"]) == 1
        assert after_link.json()["links"][0]["link_status"] == "LINKED"

        # Workspace Trip unlink is blocked until its active Opportunity/Visit child is unlinked.
        blocked_trip_unlink = client.delete(
            f"/v1/market/workspaces/{workspace_id}/trips/{trip_id}",
            headers=headers(),
        )
        assert blocked_trip_unlink.status_code == 409, blocked_trip_unlink.text

        unlink_key = str(uuid.uuid4())
        unlinked = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/"
            f"{opportunity_id}/visits/{visit_id}:unlink",
            headers=headers(key=unlink_key),
            json={"unlink_note": "Meeting moved to a different trip."},
        )
        assert unlinked.status_code == 200, unlinked.text
        assert unlinked.json()["id"] == link_id
        assert unlinked.json()["link_status"] == "UNLINKED"
        assert unlinked.json()["unlinked_by_user_id"] == ADMIN_A
        assert unlinked.json()["unlink_note"] == "Meeting moved to a different trip."

        history = client.get(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/execution",
            headers=headers(),
        )
        assert history.status_code == 200, history.text
        assert history.json()["links"][0]["link_status"] == "UNLINKED"
        assert history.json()["candidates"][0]["visit_id"] == visit_id

        relinked = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/visits",
            headers=headers(),
            json=payload,
        )
        assert relinked.status_code == 201, relinked.text
        assert relinked.json()["id"] == link_id
        assert relinked.json()["link_status"] == "LINKED"

        final_unlink = client.post(
            f"/v1/market/workspaces/{workspace_id}/opportunities/"
            f"{opportunity_id}/visits/{visit_id}:unlink",
            headers=headers(),
            json={"unlink_note": "Execution handoff test complete."},
        )
        assert final_unlink.status_code == 200, final_unlink.text
        trip_unlink = client.delete(
            f"/v1/market/workspaces/{workspace_id}/trips/{trip_id}",
            headers=headers(),
        )
        assert trip_unlink.status_code == 200, trip_unlink.text
        assert trip_unlink.json()["link_status"] == "UNLINKED"

        with org_session(UUID(ORG_A)) as session:
            assert session.get(Trip, UUID(trip_id)).version == trip_version_before
            stored_link = session.get(MarketOpportunityVisitLink, UUID(link_id))
            assert stored_link is not None and stored_link.link_status == "UNLINKED"
            event_count = session.scalar(
                select(func.count()).select_from(MarketActivityEvent).where(
                    MarketActivityEvent.organization_id == UUID(ORG_A),
                    MarketActivityEvent.workspace_id == UUID(workspace_id),
                    MarketActivityEvent.aggregate_id == UUID(opportunity_id),
                    MarketActivityEvent.event_type.like("MARKET_OPPORTUNITY_VISIT_%"),
                )
            )
            assert event_count == 4
    finally:
        cleanup(
            [workspace_id] if workspace_id else [],
            [trip_id] if trip_id else [],
            [place_id] if place_id else [],
        )


def test_cross_tenant_trip_link_rejected_by_database_fk(client: TestClient) -> None:
    workspace_id: str | None = None
    foreign_trip_id: str | None = None
    try:
        workspace_id = create_workspace(client, "Tenant-safe trip link")
        foreign_trip_id = create_trip(ORG_B, USER_B, "Foreign tenant trip")

        # Service/API refuses the foreign trip.
        response = client.post(
            f"/v1/market/workspaces/{workspace_id}/trips",
            headers=headers(),
            json={"trip_id": foreign_trip_id},
        )
        assert response.status_code == 404, response.text

        # Database composite FK protects callers which bypass the service layer.
        with org_session(UUID(ORG_A)) as session:
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(
                        MarketWorkspaceTripLink(
                            organization_id=UUID(ORG_A),
                            workspace_id=UUID(workspace_id),
                            trip_id=UUID(foreign_trip_id),
                            link_status="LINKED",
                            linked_by_user_id=UUID(ADMIN_A),
                        )
                    )
                    session.flush()
    finally:
        cleanup([workspace_id] if workspace_id else [], [foreign_trip_id] if foreign_trip_id else [])
