"""PostgreSQL integration tests for versioned question answers and evidence review."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.market import (
    MarketActivityEvent,
    MarketResearchQuestionAnswer,
    MarketResearchQuestionAnswerEvidence,
    MarketWorkspace,
)
from app.models.workflow import Evidence
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ADMIN_A = "55555555-5555-5555-5555-555555555555"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(key: str | None = None) -> dict[str, str]:
    return {
        "X-Org-Id": ORG_A,
        "X-User-Id": ADMIN_A,
        "Idempotency-Key": key or str(uuid.uuid4()),
    }


def create_workspace(client: TestClient) -> str:
    response = client.post(
        "/v1/market/workspaces",
        headers=headers(),
        json={
            "name": f"Question evidence {uuid.uuid4().hex[:8]}",
            "objective": "Verify that research questions have reviewed evidence-backed answers.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_evidence(client: TestClient, workspace_id: str, url: str, content: str) -> str:
    response = client.post(
        f"/v1/market/workspaces/{workspace_id}/evidence",
        headers=headers(),
        json={"canonical_ref": url, "content": content, "kind": "WEB_SOURCE"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_question_answer_versions_require_support_and_human_review(client: TestClient) -> None:
    workspace_id: str | None = None
    try:
        workspace_id = create_workspace(client)
        question = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions",
            headers=headers(),
            json={
                "question": "Is there independently corroborated purchasing demand?",
                "priority": 1,
                "acceptance_criteria": [
                    {"kind": "independent_sources", "minimum": 2}
                ],
            },
        )
        assert question.status_code == 201, question.text
        question_id = question.json()["id"]

        evidence_one = create_evidence(
            client,
            workspace_id,
            "https://buyer.example.com/procurement-plan",
            "Buyer published a specific procurement plan.",
        )
        evidence_two = create_evidence(
            client,
            workspace_id,
            "https://industry.example.org/demand-report",
            "Independent industry source reports similar purchasing intent.",
        )
        contradiction = create_evidence(
            client,
            workspace_id,
            "https://regulator.example.net/market-notice",
            "A regulator reports that the relevant purchasing program was suspended.",
        )

        def create_answer(
            summary: str,
            relations: list[tuple[str, str, str]],
            *,
            gap: str | None = None,
        ) -> dict:
            response = client.post(
                f"/v1/market/workspaces/{workspace_id}/questions/{question_id}/answers",
                headers=headers(),
                json={
                    "answer_summary": summary,
                    "confidence_level": "MEDIUM",
                    "assumptions": ["The published materials refer to the same purchasing category."],
                    "unknowns": ["Final approved budget"],
                    "evidence_gap": gap,
                    "evidence": [
                        {"evidence_id": evidence_id, "relation_type": relation, "rationale": rationale}
                        for evidence_id, relation, rationale in relations
                    ],
                },
            )
            assert response.status_code == 201, response.text
            return response.json()

        # One supporting domain does not meet the question's independent-source criterion.
        version_one = create_answer(
            "There is one specific buyer intent signal, but corroboration is insufficient.",
            [(evidence_one, "SUPPORTS", "A concrete procurement plan is published.")],
        )
        assert version_one["version"] == 1
        assert version_one["status"] == "NEEDS_REVIEW"
        not_enough = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions/{question_id}/answers/{version_one['id']}:accept",
            headers=headers(),
            json={"review_note": "Reviewed, but only one independent source is available."},
        )
        assert not_enough.status_code == 409, not_enough.text
        assert "independent" in not_enough.text.lower()

        # Contradictory evidence puts the answer into CONFLICTED, regardless of support count.
        version_two = create_answer(
            "Some sources indicate demand, while another source reports the program was suspended.",
            [
                (evidence_one, "SUPPORTS", "Specific buyer procurement plan."),
                (evidence_two, "SUPPORTS", "Independent industry corroboration."),
                (contradiction, "CONTRADICTS", "The program may have been suspended."),
            ],
        )
        assert version_two["version"] == 2
        assert version_two["status"] == "CONFLICTED"
        blocked = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions/{question_id}/answers/{version_two['id']}:accept",
            headers=headers(),
            json={"review_note": "Contradictory source remains unresolved."},
        )
        assert blocked.status_code == 409, blocked.text

        # A clean evidence-backed answer can be accepted by an Admin after review.
        version_three = create_answer(
            "Two independent sources support a current purchasing demand signal; budget remains unknown.",
            [
                (evidence_one, "SUPPORTS", "Buyer published a concrete procurement plan."),
                (evidence_two, "SUPPORTS", "Independent industry source corroborates demand."),
            ],
        )
        assert version_three["version"] == 3
        assert version_three["supersedes_id"] == version_two["id"]
        accepted = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions/{question_id}/answers/{version_three['id']}:accept",
            headers=headers(),
            json={"review_note": "Both sources and the two-source acceptance criterion were reviewed."},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == "ACCEPTED"
        assert accepted.json()["reviewed_by_user_id"] == ADMIN_A
        assert accepted.json()["reviewed_at"] is not None

        question_after = client.get(
            f"/v1/market/workspaces/{workspace_id}/questions",
            headers=headers(),
        )
        assert question_after.status_code == 200, question_after.text
        stored_question = next(
            item for item in question_after.json()["items"] if item["id"] == question_id
        )
        assert stored_question["status"] == "ANSWERED"
        assert stored_question["answer_summary"] == version_three["answer_summary"]

        # A new answer is appended; accepting it supersedes the previous accepted version.
        version_four = create_answer(
            "The demand signal remains corroborated, with the budget approval still outstanding.",
            [
                (evidence_one, "SUPPORTS", "Procurement plan remains published."),
                (evidence_two, "SUPPORTS", "Independent demand report remains published."),
            ],
        )
        assert version_four["version"] == 4
        accepted_four = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions/{question_id}/answers/{version_four['id']}:accept",
            headers=headers(),
            json={"review_note": "Updated answer reviewed against the same two independent sources."},
        )
        assert accepted_four.status_code == 200, accepted_four.text
        assert accepted_four.json()["status"] == "ACCEPTED"

        versions = client.get(
            f"/v1/market/workspaces/{workspace_id}/questions/{question_id}/answers",
            headers=headers(),
        )
        assert versions.status_code == 200, versions.text
        assert [item["version"] for item in versions.json()] == [4, 3, 2, 1]
        assert [item["status"] for item in versions.json()].count("ACCEPTED") == 1
        assert next(item for item in versions.json() if item["id"] == version_three["id"])["status"] == "SUPERSEDED"

        with org_session(UUID(ORG_A)) as session:
            answer_ids = [UUID(item["id"]) for item in versions.json()]
            assert session.scalar(
                select(func.count()).select_from(MarketResearchQuestionAnswerEvidence).where(
                    MarketResearchQuestionAnswerEvidence.organization_id == UUID(ORG_A),
                    MarketResearchQuestionAnswerEvidence.answer_id.in_(answer_ids),
                )
            ) == 8
            answer_events = session.scalar(
                select(func.count()).select_from(MarketActivityEvent).where(
                    MarketActivityEvent.organization_id == UUID(ORG_A),
                    MarketActivityEvent.workspace_id == UUID(workspace_id),
                    MarketActivityEvent.aggregate_id == UUID(question_id),
                    MarketActivityEvent.event_type.in_(
                        [
                            "RESEARCH_QUESTION_ANSWER_CREATED",
                            "RESEARCH_QUESTION_ANSWER_ACCEPTED",
                        ]
                    ),
                )
            )
            assert answer_events == 6
    finally:
        if workspace_id is not None:
            with org_session(UUID(ORG_A)) as session:
                # Workspace cascade removes QuestionAnswer and its evidence links before
                # deleting the Workspace's polymorphic Evidence rows.
                session.execute(
                    delete(MarketWorkspace).where(
                        MarketWorkspace.organization_id == UUID(ORG_A),
                        MarketWorkspace.id == UUID(workspace_id),
                    )
                )
                session.execute(
                    delete(Evidence).where(
                        Evidence.organization_id == UUID(ORG_A),
                        Evidence.subject_type == "market_workspace",
                        Evidence.subject_id == UUID(workspace_id),
                    )
                )


def test_question_answer_rejects_cross_workspace_evidence(client: TestClient) -> None:
    workspace_one: str | None = None
    workspace_two: str | None = None
    try:
        workspace_one = create_workspace(client)
        workspace_two = create_workspace(client)
        question = client.post(
            f"/v1/market/workspaces/{workspace_two}/questions",
            headers=headers(),
            json={"question": "Can this conclusion be supported?"},
        )
        assert question.status_code == 201, question.text
        evidence_id = create_evidence(
            client,
            workspace_one,
            "https://other-workspace.example.com/source",
            "Evidence scoped to a different Workspace.",
        )
        response = client.post(
            f"/v1/market/workspaces/{workspace_two}/questions/{question.json()['id']}/answers",
            headers=headers(),
            json={
                "answer_summary": "This answer must not use evidence from another Workspace.",
                "evidence": [
                    {"evidence_id": evidence_id, "relation_type": "SUPPORTS"}
                ],
            },
        )
        assert response.status_code == 409, response.text
    finally:
        for workspace_id in [workspace_one, workspace_two]:
            if workspace_id is not None:
                with org_session(UUID(ORG_A)) as session:
                    session.execute(
                        delete(MarketWorkspace).where(
                            MarketWorkspace.organization_id == UUID(ORG_A),
                            MarketWorkspace.id == UUID(workspace_id),
                        )
                    )
                    session.execute(
                        delete(Evidence).where(
                            Evidence.organization_id == UUID(ORG_A),
                            Evidence.subject_type == "market_workspace",
                            Evidence.subject_id == UUID(workspace_id),
                        )
                    )
