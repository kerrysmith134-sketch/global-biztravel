"""Evidence versioning and user-reviewed MarketInsight PostgreSQL acceptance tests."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.market import MarketActivityEvent, MarketInsight, MarketInsightEvidence, MarketWorkspace
from app.models.workflow import Evidence
from app.services import evidence_service
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
ADMIN_A = "55555555-5555-5555-5555-555555555555"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(key: str | None = None, org: str = ORG_A) -> dict[str, str]:
    return {
        "X-Org-Id": org,
        "X-User-Id": ADMIN_A,
        "Idempotency-Key": key or str(uuid.uuid4()),
    }


def create_workspace(client: TestClient, label: str) -> str:
    response = client.post(
        "/v1/market/workspaces",
        headers=headers(),
        json={"name": f"{label} {uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def cleanup_workspace(workspace_id: str | None) -> None:
    if workspace_id is None:
        return
    with org_session(UUID(ORG_A)) as session:
        # Workspace deletion cascades Insight and InsightEvidence first; Evidence remains append-only.
        session.execute(
            delete(MarketWorkspace).where(MarketWorkspace.id == UUID(workspace_id))
        )
        session.execute(
            delete(Evidence).where(
                Evidence.organization_id == UUID(ORG_A),
                Evidence.subject_type == "market_workspace",
                Evidence.subject_id == UUID(workspace_id),
            )
        )


def test_market_evidence_content_versioning_is_idempotent(client: TestClient) -> None:
    workspace_id: str | None = None
    evidence_ids: set[str] = set()
    try:
        workspace_id = create_workspace(client, "Evidence version test")
        url = "HTTPS://EXAMPLE.COM:443/research/report?region=ke#section"
        base = {
            "canonical_ref": url,
            "content": "Market demand grew in the latest reporting period.",
            "kind": "WEB_SOURCE",
            "source": "Example Research",
            "payload": {"capture_method": "test"},
        }

        first = client.post(
            f"/v1/market/workspaces/{workspace_id}/evidence",
            headers=headers(),
            json=base,
        )
        assert first.status_code == 201, first.text
        first_row = first.json()
        first_id = first_row["id"]
        evidence_ids.add(first_id)
        assert first_row["canonical_ref"] == "https://example.com/research/report?region=ke"
        assert first_row["content_hash"]
        assert first_row["payload"]["content"] == base["content"]
        assert first_row["published_at"] is None

        # Same source + same body is idempotent even with a new HTTP idempotency key.
        replay = client.post(
            f"/v1/market/workspaces/{workspace_id}/evidence",
            headers=headers(),
            json=base,
        )
        assert replay.status_code == 201, replay.text
        assert replay.json()["id"] == first_id

        # Changed body under the same canonical source creates a new immutable evidence version.
        changed = {**base, "content": "Market demand declined in the latest reporting period."}
        second = client.post(
            f"/v1/market/workspaces/{workspace_id}/evidence",
            headers=headers(),
            json=changed,
        )
        assert second.status_code == 201, second.text
        second_row = second.json()
        assert second_row["id"] != first_id
        evidence_ids.add(second_row["id"])
        assert second_row["canonical_ref"] == first_row["canonical_ref"]
        assert second_row["content_hash"] != first_row["content_hash"]

        # Different source URLs never collapse, even when their source body is identical.
        other_source = {
            **base,
            "canonical_ref": "https://other.example.com/same-report",
        }
        third = client.post(
            f"/v1/market/workspaces/{workspace_id}/evidence",
            headers=headers(),
            json=other_source,
        )
        assert third.status_code == 201, third.text
        third_row = third.json()
        assert third_row["content_hash"] == first_row["content_hash"]
        assert third_row["id"] != first_id
        evidence_ids.add(third_row["id"])

        with org_session(UUID(ORG_A)) as session:
            versions = session.scalars(
                select(Evidence).where(
                    Evidence.organization_id == UUID(ORG_A),
                    Evidence.subject_type == "market_workspace",
                    Evidence.subject_id == UUID(workspace_id),
                )
            ).all()
            assert len(versions) == 3
            assert {str(row.id) for row in versions} == evidence_ids
            activity_count = session.scalar(
                select(func.count()).select_from(MarketActivityEvent).where(
                    MarketActivityEvent.organization_id == UUID(ORG_A),
                    MarketActivityEvent.workspace_id == UUID(workspace_id),
                    MarketActivityEvent.event_type == "MARKET_EVIDENCE_RECORDED",
                )
            )
            assert activity_count == 3  # The same source/body replay adds no duplicate event.

        with org_session(UUID(ORG_B)) as session:
            assert session.scalar(
                select(func.count()).select_from(Evidence).where(
                    Evidence.id.in_([UUID(item) for item in evidence_ids])
                )
            ) == 0
    finally:
        cleanup_workspace(workspace_id)


def test_insight_requires_evidence_and_human_review(client: TestClient) -> None:
    workspace_id: str | None = None
    try:
        workspace_id = create_workspace(client, "Insight review test")
        evidence = client.post(
            f"/v1/market/workspaces/{workspace_id}/evidence",
            headers=headers(),
            json={
                "canonical_ref": "https://example.com/market-proof",
                "content": "Two independent buyers published procurement plans.",
                "kind": "WEB_SOURCE",
            },
        )
        assert evidence.status_code == 201, evidence.text
        evidence_id = evidence.json()["id"]

        # A judgement without evidence must state the evidence gap explicitly.
        ungrounded = client.post(
            f"/v1/market/workspaces/{workspace_id}/insights",
            headers=headers(),
            json={
                "insight_key": "buyer-demand",
                "insight_type": "demand_signal",
                "statement": "Demand is accelerating.",
            },
        )
        assert ungrounded.status_code == 400, ungrounded.text

        draft_payload = {
            "insight_key": "buyer-demand",
            "insight_type": "demand_signal",
            "statement": "Published procurement plans indicate emerging demand.",
            "confidence_level": "MEDIUM",
            "evidence": [
                {
                    "evidence_id": evidence_id,
                    "relation_type": "SUPPORTS",
                    "rationale": "The source describes a concrete purchasing plan.",
                }
            ],
        }
        draft = client.post(
            f"/v1/market/workspaces/{workspace_id}/insights",
            headers=headers(),
            json=draft_payload,
        )
        assert draft.status_code == 201, draft.text
        insight_id = draft.json()["id"]
        assert draft.json()["status"] == "NEEDS_REVIEW"
        assert draft.json()["version"] == 1
        assert len(draft.json()["evidence"]) == 1

        accepted = client.post(
            f"/v1/market/workspaces/{workspace_id}/insights/{insight_id}:accept",
            headers=headers(),
            json={"review_note": "Source content checked; accepted as a working judgment."},
        )
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["status"] == "ACCEPTED"
        assert accepted.json()["reviewed_by_user_id"] == ADMIN_A
        assert accepted.json()["reviewed_at"] is not None

        # A changed judgment is appended as a new version, never an in-place overwrite.
        revision = client.post(
            f"/v1/market/workspaces/{workspace_id}/insights",
            headers=headers(),
            json={
                **draft_payload,
                "statement": "Demand is emerging in two identified buyer segments.",
            },
        )
        assert revision.status_code == 201, revision.text
        assert revision.json()["version"] == 2
        assert revision.json()["supersedes_id"] == insight_id
        second_id = revision.json()["id"]
        accepted_revision = client.post(
            f"/v1/market/workspaces/{workspace_id}/insights/{second_id}:accept",
            headers=headers(),
            json={"review_note": "Second version reviewed and adopted."},
        )
        assert accepted_revision.status_code == 200, accepted_revision.text
        assert accepted_revision.json()["status"] == "ACCEPTED"

        contradiction = client.post(
            f"/v1/market/workspaces/{workspace_id}/evidence",
            headers=headers(),
            json={
                "canonical_ref": "https://example.org/counter-signal",
                "content": "Buyer investment plans were postponed.",
                "kind": "WEB_SOURCE",
            },
        )
        assert contradiction.status_code == 201, contradiction.text
        conflicted = client.post(
            f"/v1/market/workspaces/{workspace_id}/insights",
            headers=headers(),
            json={
                "insight_key": "buyer-demand-conflicted",
                "insight_type": "demand_signal",
                "statement": "Demand is uncertain because source signals conflict.",
                "confidence_level": "LOW",
                "evidence": [
                    {"evidence_id": evidence_id, "relation_type": "SUPPORTS"},
                    {"evidence_id": contradiction.json()["id"], "relation_type": "CONTRADICTS"},
                ],
            },
        )
        assert conflicted.status_code == 201, conflicted.text
        conflicted_id = conflicted.json()["id"]
        assert conflicted.json()["status"] == "CONFLICTED"
        blocked = client.post(
            f"/v1/market/workspaces/{workspace_id}/insights/{conflicted_id}:accept",
            headers=headers(),
            json={},
        )
        assert blocked.status_code == 409, blocked.text

        insufficient = client.post(
            f"/v1/market/workspaces/{workspace_id}/insights",
            headers=headers(),
            json={
                "insight_key": "coverage-gap",
                "insight_type": "unknown",
                "statement": "There is not enough evidence to assess market size.",
                "confidence_level": "INSUFFICIENT",
                "insufficient_evidence_reason": "No reliable market sizing source has been captured.",
            },
        )
        assert insufficient.status_code == 201, insufficient.text
        blocked_insufficient = client.post(
            f"/v1/market/workspaces/{workspace_id}/insights/{insufficient.json()['id']}:accept",
            headers=headers(),
            json={},
        )
        assert blocked_insufficient.status_code == 409, blocked_insufficient.text

        with org_session(UUID(ORG_A)) as session:
            stored = session.get(MarketInsight, UUID(insight_id))
            assert stored is not None and stored.status == "SUPERSEDED"
            assert session.scalar(
                select(func.count()).select_from(MarketInsightEvidence).where(
                    MarketInsightEvidence.insight_id == UUID(second_id)
                )
            ) == 1
    finally:
        cleanup_workspace(workspace_id)


def test_insight_cannot_reference_another_workspaces_evidence(client: TestClient) -> None:
    workspace_one: str | None = None
    workspace_two: str | None = None
    try:
        workspace_one = create_workspace(client, "Evidence owner workspace")
        workspace_two = create_workspace(client, "Separate workspace")
        evidence = client.post(
            f"/v1/market/workspaces/{workspace_one}/evidence",
            headers=headers(),
            json={
                "canonical_ref": "https://example.com/private-proof",
                "content": "Evidence scoped to another Workspace.",
                "kind": "WEB_SOURCE",
            },
        )
        assert evidence.status_code == 201, evidence.text
        response = client.post(
            f"/v1/market/workspaces/{workspace_two}/insights",
            headers=headers(),
            json={
                "insight_key": "cross-workspace",
                "insight_type": "signal",
                "statement": "This must not attach foreign Workspace evidence.",
                "evidence": [
                    {"evidence_id": evidence.json()["id"], "relation_type": "SUPPORTS"}
                ],
            },
        )
        assert response.status_code == 409, response.text

        valid_insight = client.post(
            f"/v1/market/workspaces/{workspace_one}/insights",
            headers=headers(),
            json={
                "insight_key": "workspace-one-proof",
                "insight_type": "signal",
                "statement": "This evidence belongs to the first Workspace.",
                "evidence": [
                    {"evidence_id": evidence.json()["id"], "relation_type": "SUPPORTS"}
                ],
            },
        )
        assert valid_insight.status_code == 201, valid_insight.text

        # The composite database FK must reject cross-tenant references even if
        # a caller bypasses the service-level scope validation.
        with org_session(UUID(ORG_B)) as session:
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(
                        MarketInsightEvidence(
                            organization_id=UUID(ORG_B),
                            insight_id=UUID(valid_insight.json()["id"]),
                            evidence_id=UUID(evidence.json()["id"]),
                            relation_type="SUPPORTS",
                        )
                    )
                    session.flush()
    finally:
        cleanup_workspace(workspace_one)
        cleanup_workspace(workspace_two)


def test_evidence_intelligence_report_detects_duplicate_and_source_versions(
    client: TestClient,
) -> None:
    workspace_id: str | None = None
    evidence_ids: list[str] = []
    try:
        workspace_id = create_workspace(client, "Evidence intelligence report")
        original_body = "A named institution published an identical procurement notice in 2026."
        records = [
            {
                "canonical_ref": "https://example.com/reports/procurement",
                "content": original_body,
                "kind": "WEB_SEARCH_RESULT",
                "source": "Example Publisher",
            },
            {
                "canonical_ref": "https://example.com/reports/procurement",
                "content": "A revised version of the procurement notice was published later in 2026.",
                "kind": "WEB_SEARCH_RESULT",
                "source": "Example Publisher",
            },
            {
                "canonical_ref": "https://other.example.org/republication",
                "content": original_body,
                "kind": "WEB_SEARCH_RESULT",
                "source": "Other Publisher",
            },
        ]
        for payload in records:
            created = client.post(
                f"/v1/market/workspaces/{workspace_id}/evidence",
                headers=headers(),
                json=payload,
            )
            assert created.status_code == 201, created.text
            evidence_ids.append(created.json()["id"])

        response = client.get(
            f"/v1/market/workspaces/{workspace_id}/evidence-intelligence",
            headers=headers(),
        )
        assert response.status_code == 200, response.text
        report = response.json()
        assert report["workspace_id"] == workspace_id
        assert report["total_evidence_count"] == 3
        assert report["analyzed_evidence_count"] == 3
        assert report["is_truncated"] is False
        assert report["distinct_host_count"] == 2
        assert report["duplicate_content_group_count"] == 1
        assert report["duplicate_content_evidence_count"] == 2
        assert report["multi_version_source_count"] == 1
        assert report["multi_version_evidence_count"] == 2
        assert report["unverified_count"] == 3
        assert report["missing_publication_date_count"] == 3
        assert report["unextracted_search_result_count"] == 3

        by_id = {item["evidence_id"]: item for item in report["items"]}
        first, revised, copied = (by_id[item] for item in evidence_ids)
        assert first["origin_assessment"] == "DUPLICATE_CONTENT"
        assert copied["origin_assessment"] == "DUPLICATE_CONTENT"
        assert set(first["duplicate_content_evidence_ids"]) == {evidence_ids[2]}
        assert set(copied["duplicate_content_evidence_ids"]) == {evidence_ids[0]}
        assert revised["same_source_version_evidence_ids"] == [evidence_ids[0]]
        assert set(first["same_host_evidence_ids"]) == {evidence_ids[1]}
        assert "SEARCH_RESULT_NOT_PAGE_EXTRACTED" in first["issues"]
        assert any("不证明出版方相互独立" in item for item in report["caveats"])

        # The same report cannot be used to enumerate another tenant's workspace.
        denied = client.get(
            f"/v1/market/workspaces/{workspace_id}/evidence-intelligence",
            headers=headers(org=ORG_B),
        )
        assert denied.status_code == 404, denied.text
    finally:
        cleanup_workspace(workspace_id)


def test_legacy_evidence_record_many_contract_is_unchanged() -> None:
    workspace_id: str | None = None
    try:
        with org_session(UUID(ORG_A)) as session:
            workspace = MarketWorkspace(
                organization_id=UUID(ORG_A),
                name=f"Legacy evidence test {uuid.uuid4().hex[:8]}",
                status="DRAFT",
                settings={},
                owner_user_id=UUID(ADMIN_A),
                created_by_user_id=UUID(ADMIN_A),
            )
            session.add(workspace)
            session.flush()
            workspace_id = str(workspace.id)
            item = {
                "kind": "LEGACY_REFERENCE",
                "ref": "legacy://same-reference",
                "source": "existing caller",
                "payload": {"unchanged": True},
            }
            first = evidence_service.record_many(
                session,
                UUID(ORG_A),
                subject_type="LEGACY_TEST",
                subject_id=workspace.id,
                items=[item],
            )
            second = evidence_service.record_many(
                session,
                UUID(ORG_A),
                subject_type="LEGACY_TEST",
                subject_id=workspace.id,
                items=[item],
            )
            assert first == 1
            assert second == 0
            rows = session.scalars(
                select(Evidence).where(
                    Evidence.organization_id == UUID(ORG_A),
                    Evidence.subject_type == "LEGACY_TEST",
                    Evidence.subject_id == workspace.id,
                )
            ).all()
            assert len(rows) == 1
            assert rows[0].ref == "legacy://same-reference"
            assert rows[0].payload == {"unchanged": True}
    finally:
        if workspace_id is not None:
            with org_session(UUID(ORG_A)) as session:
                session.execute(
                    delete(Evidence).where(
                        Evidence.organization_id == UUID(ORG_A),
                        Evidence.subject_type == "LEGACY_TEST",
                        Evidence.subject_id == UUID(workspace_id),
                    )
                )
                session.execute(
                    delete(MarketWorkspace).where(
                        MarketWorkspace.id == UUID(workspace_id)
                    )
                )
