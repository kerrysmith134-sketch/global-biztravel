"""Market Intelligence H1.2 foundation acceptance tests (real PostgreSQL)."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from app.db import org_session, owner_session
from app.main import app
from app.models.market import MarketWorkspace
from fastapi.testclient import TestClient
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
ADMIN_A = "55555555-5555-5555-5555-555555555555"
USER_B = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(user: str = ADMIN_A, org: str = ORG_A, *, key: str | None = None) -> dict[str, str]:
    return {
        "X-Org-Id": org,
        "X-User-Id": user,
        "Idempotency-Key": key or str(uuid.uuid4()),
    }


def create_workspace(client: TestClient, *, label: str) -> dict:
    response = client.post(
        "/v1/market/workspaces",
        headers=headers(),
        json={
            "name": f"Market workspace {label}",
            "objective": "Explore an unfamiliar commercial market",
            "raw_input": (
                "We provide a specialist service for a narrowly defined customer group. "
                "Research the target region, relevant buying process, and practical route to market."
            ),
            "settings": {"locale": "en"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def cleanup(workspace_ids: list[str]) -> None:
    if not workspace_ids:
        return
    with owner_session(UUID(ORG_A)) as session:
        session.execute(
            delete(MarketWorkspace).where(
                MarketWorkspace.organization_id == UUID(ORG_A),
                MarketWorkspace.id.in_([UUID(value) for value in workspace_ids]),
            )
        )


def test_workspace_preserves_raw_input_and_create_is_idempotent(client: TestClient) -> None:
    key = str(uuid.uuid4())
    body = {
        "name": f"Workspace {uuid.uuid4().hex[:10]}",
        "objective": "Identify a feasible route to market",
        "raw_input": (
            "Our business is a specialist provider in a niche that is not in any built-in taxonomy. "
            "We want to understand demand and potential partners in two cities."
        ),
        "settings": {"research_language": "en"},
    }
    workspace_ids: list[str] = []
    request_headers = headers(key=key)
    try:
        first = client.post("/v1/market/workspaces", headers=request_headers, json=body)
        assert first.status_code == 201, first.text
        workspace_id = first.json()["id"]
        workspace_ids.append(workspace_id)

        replay = client.post("/v1/market/workspaces", headers=request_headers, json=body)
        assert replay.status_code == 201, replay.text
        assert replay.headers.get("Idempotent-Replay") == "true"
        assert replay.json()["id"] == workspace_id

        contexts = client.get(
            f"/v1/market/workspaces/{workspace_id}/contexts", headers=headers()
        )
        assert contexts.status_code == 200, contexts.text
        assert len(contexts.json()) == 1
        snapshot = contexts.json()[0]
        assert snapshot["version"] == 1
        assert snapshot["raw_input"] == body["raw_input"]
        assert snapshot["is_current"] is True
        assert snapshot["extraction_status"] == "NOT_STARTED"
        assert snapshot["extracted_context"]["industries"] == []
        assert first.json()["current_context_version"] == 1
    finally:
        cleanup(workspace_ids)


def test_context_versions_are_immutable_and_explicitly_adopted(client: TestClient) -> None:
    workspace_ids: list[str] = []
    try:
        workspace = create_workspace(client, label=uuid.uuid4().hex[:8])
        workspace_id = workspace["id"]
        workspace_ids.append(workspace_id)
        original = client.get(
            f"/v1/market/workspaces/{workspace_id}/contexts", headers=headers()
        ).json()[0]

        second_input = "Updated brief: validate a local professional-services distribution model."
        second = client.post(
            f"/v1/market/workspaces/{workspace_id}/contexts",
            headers=headers(),
            json={
                "raw_input": second_input,
                "user_overrides": {
                    "industries": [{"label": "user-defined specialist services", "source": "USER_EDIT"}]
                },
            },
        )
        assert second.status_code == 201, second.text
        version_two = second.json()
        assert version_two["version"] == 2
        assert version_two["is_current"] is False

        # The original user input remains byte-for-byte retrievable.
        before_adopt = client.get(
            f"/v1/market/workspaces/{workspace_id}/contexts", headers=headers()
        )
        versions = {item["version"]: item for item in before_adopt.json()}
        assert versions[1]["raw_input"] == original["raw_input"]
        assert versions[2]["raw_input"] == second_input

        adopted = client.post(
            f"/v1/market/workspaces/{workspace_id}/contexts/{version_two['id']}:adopt",
            headers=headers(),
        )
        assert adopted.status_code == 200, adopted.text
        assert adopted.json()["is_current"] is True

        third_input = "Third brief: focus on buyer accessibility and implementation constraints."
        third = client.post(
            f"/v1/market/workspaces/{workspace_id}/contexts",
            headers=headers(),
            json={"raw_input": third_input},
        )
        assert third.status_code == 201, third.text
        version_three = third.json()
        assert version_three["version"] == 3
        # Explicit user corrections carry forward from the adopted context version.
        assert version_three["user_overrides"] == version_two["user_overrides"]

        detail = client.get(
            f"/v1/market/workspaces/{workspace_id}", headers=headers()
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["current_context_version"] == 2
    finally:
        cleanup(workspace_ids)


def test_questions_are_workspace_scoped_and_status_is_not_client_mutable(
    client: TestClient,
) -> None:
    workspace_ids: list[str] = []
    try:
        workspace_one = create_workspace(client, label=uuid.uuid4().hex[:8])
        workspace_two = create_workspace(client, label=uuid.uuid4().hex[:8])
        workspace_ids.extend([workspace_one["id"], workspace_two["id"]])

        parent = client.post(
            f"/v1/market/workspaces/{workspace_one['id']}/questions",
            headers=headers(),
            json={
                "question": "Which customer segments are evidenced by public sources?",
                "priority": 1,
                "acceptance_criteria": {"requires_independent_sources": True},
            },
        )
        assert parent.status_code == 201, parent.text

        child = client.post(
            f"/v1/market/workspaces/{workspace_one['id']}/questions",
            headers=headers(),
            json={
                "question": "Which local regulations affect access to those customers?",
                "parent_question_id": parent.json()["id"],
                "priority": 2,
            },
        )
        assert child.status_code == 201, child.text
        assert child.json()["parent_question_id"] == parent.json()["id"]

        updated = client.patch(
            f"/v1/market/questions/{child.json()['id']}",
            headers=headers(),
            json={"priority": 1, "rationale": "This blocks the route-to-market decision."},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["priority"] == 1

        wrong_parent = client.post(
            f"/v1/market/workspaces/{workspace_two['id']}/questions",
            headers=headers(),
            json={
                "question": "A question in a different workspace",
                "parent_question_id": parent.json()["id"],
            },
        )
        assert wrong_parent.status_code == 404, wrong_parent.text

        illegal_status = client.patch(
            f"/v1/market/questions/{child.json()['id']}",
            headers=headers(),
            json={"status": "ANSWERED"},
        )
        assert illegal_status.status_code == 422, illegal_status.text

        activity = client.get(
            f"/v1/market/workspaces/{workspace_one['id']}/activity", headers=headers()
        )
        assert activity.status_code == 200, activity.text
        event_types = {item["event_type"] for item in activity.json()["items"]}
        assert "WORKSPACE_CREATED" in event_types
        assert "RESEARCH_QUESTION_CREATED" in event_types
        assert "RESEARCH_QUESTION_UPDATED" in event_types
    finally:
        cleanup(workspace_ids)


def test_workspace_is_not_visible_to_another_tenant(client: TestClient) -> None:
    workspace_ids: list[str] = []
    try:
        workspace = create_workspace(client, label=uuid.uuid4().hex[:8])
        workspace_ids.append(workspace["id"])
        response = client.get(
            f"/v1/market/workspaces/{workspace['id']}",
            headers=headers(user=USER_B, org=ORG_B),
        )
        assert response.status_code == 404, response.text

        contexts = client.get(
            f"/v1/market/workspaces/{workspace['id']}/contexts",
            headers=headers(user=USER_B, org=ORG_B),
        )
        assert contexts.status_code == 404, contexts.text
    finally:
        cleanup(workspace_ids)

def test_current_context_pointer_is_enforced_by_postgresql(client: TestClient) -> None:
    workspace_ids: list[str] = []
    try:
        workspace = create_workspace(client, label=uuid.uuid4().hex[:8])
        workspace_ids.append(workspace["id"])

        # The workspace pointer must reference a real context version in the same workspace.
        with pytest.raises(IntegrityError):
            with org_session(UUID(ORG_A)) as session:
                session.execute(
                    text(
                        "UPDATE market_workspaces SET current_context_version = 999 "
                        "WHERE organization_id = CAST(:org_id AS uuid) AND id = CAST(:workspace_id AS uuid)"
                    ),
                    {"org_id": ORG_A, "workspace_id": workspace["id"]},
                )
                session.execute(
                    text("SET CONSTRAINTS fk_market_workspaces_current_context_version IMMEDIATE")
                )
    finally:
        cleanup(workspace_ids)


def test_question_dependencies_are_distinct_cycle_safe_and_audited(
    client: TestClient,
) -> None:
    workspace_ids: list[str] = []
    try:
        workspace = create_workspace(client, label=uuid.uuid4().hex[:8])
        other_workspace = create_workspace(client, label=uuid.uuid4().hex[:8])
        workspace_ids.extend([workspace["id"], other_workspace["id"]])

        def create_question(workspace_id: str, title: str) -> dict:
            response = client.post(
                f"/v1/market/workspaces/{workspace_id}/questions",
                headers=headers(),
                json={"question": title},
            )
            assert response.status_code == 201, response.text
            return response.json()

        q_a = create_question(workspace["id"], "Map the customer decision process")
        q_b = create_question(workspace["id"], "Identify regulatory prerequisites")
        q_c = create_question(workspace["id"], "Validate the local partner landscape")
        q_other = create_question(other_workspace["id"], "Unrelated workspace question")

        first = client.post(
            f"/v1/market/workspaces/{workspace['id']}/questions/{q_a['id']}/dependencies",
            headers=headers(),
            json={"depends_on_question_id": q_b["id"]},
        )
        assert first.status_code == 201, first.text
        dependency_id = first.json()["id"]

        second = client.post(
            f"/v1/market/workspaces/{workspace['id']}/questions/{q_b['id']}/dependencies",
            headers=headers(),
            json={"depends_on_question_id": q_c["id"]},
        )
        assert second.status_code == 201, second.text

        # Duplicate edge with a fresh idempotency key is a safe no-op.
        duplicate = client.post(
            f"/v1/market/workspaces/{workspace['id']}/questions/{q_a['id']}/dependencies",
            headers=headers(),
            json={"depends_on_question_id": q_b["id"]},
        )
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["id"] == dependency_id

        # Parent/child hierarchy and dependency graph are separate concepts.
        listed = client.get(
            f"/v1/market/workspaces/{workspace['id']}/questions/{q_a['id']}/dependencies",
            headers=headers(),
        )
        assert listed.status_code == 200, listed.text
        assert [item["depends_on_question_id"] for item in listed.json()] == [q_b["id"]]

        self_dependency = client.post(
            f"/v1/market/workspaces/{workspace['id']}/questions/{q_a['id']}/dependencies",
            headers=headers(),
            json={"depends_on_question_id": q_a["id"]},
        )
        assert self_dependency.status_code == 422, self_dependency.text

        # A -> B -> C already exists, so C -> A would close a cycle.
        cycle = client.post(
            f"/v1/market/workspaces/{workspace['id']}/questions/{q_c['id']}/dependencies",
            headers=headers(),
            json={"depends_on_question_id": q_a["id"]},
        )
        assert cycle.status_code == 422, cycle.text

        cross_workspace = client.post(
            f"/v1/market/workspaces/{workspace['id']}/questions/{q_a['id']}/dependencies",
            headers=headers(),
            json={"depends_on_question_id": q_other["id"]},
        )
        assert cross_workspace.status_code == 404, cross_workspace.text

        removed = client.delete(
            f"/v1/market/workspaces/{workspace['id']}/questions/"
            f"{q_a['id']}/dependencies/{dependency_id}",
            headers=headers(),
        )
        assert removed.status_code == 200, removed.text
        assert removed.json() == {"dependency_id": dependency_id, "deleted": True}

        listed_after_delete = client.get(
            f"/v1/market/workspaces/{workspace['id']}/questions/{q_a['id']}/dependencies",
            headers=headers(),
        )
        assert listed_after_delete.status_code == 200
        assert listed_after_delete.json() == []

        activity = client.get(
            f"/v1/market/workspaces/{workspace['id']}/activity", headers=headers()
        )
        assert activity.status_code == 200, activity.text
        event_types = {item["event_type"] for item in activity.json()["items"]}
        assert "RESEARCH_QUESTION_DEPENDENCY_CREATED" in event_types
        assert "RESEARCH_QUESTION_DEPENDENCY_REMOVED" in event_types
    finally:
        cleanup(workspace_ids)

