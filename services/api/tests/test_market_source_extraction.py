"""PostgreSQL integration tests for user-triggered market source extraction."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID

import pytest
from app.db import org_session
from app.integrations.research_page import ResearchPage
from app.main import app
from app.models.market import MarketEvidenceDerivation, MarketWorkspace
from app.models.reliability import BackgroundJob
from app.models.workflow import AgentRun, Evidence, WorkflowInstance
from app.services import job_service
from app.workers.tasks import handle_market_source_page_extraction
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

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
            "name": f"Source extraction {uuid.uuid4().hex[:8]}",
            "objective": "Inspect a source selected by the traveler and retain its provenance.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def create_search_evidence(client: TestClient, workspace_id: str) -> str:
    response = client.post(
        f"/v1/market/workspaces/{workspace_id}/evidence",
        headers=headers(),
        json={
            "canonical_ref": "https://example.com/research",
            "content": "Search result title and short snippet; this is not the fetched page body.",
            "kind": "WEB_SEARCH_RESULT",
            "source": "test-search-provider",
            "payload": {"query": "user-approved research query"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


class FakePageProvider:
    name = "test-safe-http"

    def __init__(self, page: ResearchPage) -> None:
        self.page = page
        self.requested: list[str] = []

    @property
    def configured(self) -> bool:
        return True

    def fetch(self, url: str) -> ResearchPage:
        self.requested.append(url)
        return self.page


def page_fixture() -> ResearchPage:
    full_text = (
        "A selected source page contains a substantial article body with attributable "
        "details and context. This text remains unverified until independent corroboration. "
    ) * 20
    return ResearchPage(
        requested_url="https://example.com/research",
        final_url="https://example.com/research/article",
        title="Selected research article",
        description="A test article",
        author="Test publisher",
        published_at=datetime(2026, 11, 20, 10, 30, tzinfo=UTC),
        published_at_raw="2026-11-20T10:30:00Z",
        observed_canonical_url="https://example.com/canonical",
        language="en",
        excerpt=full_text[:1200],
        content_hash=sha256(full_text.encode("utf-8")).hexdigest(),
        content_chars=len(full_text),
        content_type="text/html",
        retrieved_at=datetime.now(UTC),
    )


def execute_job(job_id: str) -> dict:
    with org_session(UUID(ORG_A)) as session:
        job = session.get(BackgroundJob, UUID(job_id))
        assert job is not None
        job.status = "RUNNING"
        job.attempts = max(job.attempts, 1)
        job.locked_by = "market-source-extraction-test-worker"
        result = handle_market_source_page_extraction(session, job)
        job_service.complete(session, job.id, result=result)
        return result


def cleanup(
    workspace_ids: list[str],
    workflow_ids: list[str],
    job_ids: list[str],
) -> None:
    with org_session(UUID(ORG_A)) as session:
        # Workspace deletion cascades its Workflow links and derivation lineage.
        if workspace_ids:
            session.execute(
                delete(MarketWorkspace).where(
                    MarketWorkspace.organization_id == UUID(ORG_A),
                    MarketWorkspace.id.in_([UUID(item) for item in workspace_ids]),
                )
            )
            session.execute(
                delete(Evidence).where(
                    Evidence.organization_id == UUID(ORG_A),
                    Evidence.subject_type == "market_workspace",
                    Evidence.subject_id.in_([UUID(item) for item in workspace_ids]),
                )
            )
        if workflow_ids:
            workflow_uuids = [UUID(item) for item in workflow_ids]
            session.execute(
                delete(AgentRun).where(
                    AgentRun.organization_id == UUID(ORG_A),
                    AgentRun.workflow_id.in_(workflow_uuids),
                )
            )
            session.execute(
                delete(WorkflowInstance).where(
                    WorkflowInstance.organization_id == UUID(ORG_A),
                    WorkflowInstance.id.in_(workflow_uuids),
                )
            )
        if job_ids:
            session.execute(
                delete(BackgroundJob).where(
                    BackgroundJob.organization_id == UUID(ORG_A),
                    BackgroundJob.id.in_([UUID(item) for item in job_ids]),
                )
            )


def test_user_selected_source_is_extracted_as_new_unverified_evidence(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_ids: list[str] = []
    workflow_ids: list[str] = []
    job_ids: list[str] = []
    provider = FakePageProvider(page_fixture())
    monkeypatch.setattr(
        "app.workers.tasks.get_research_page_provider",
        lambda: provider,
    )
    try:
        workspace_id = create_workspace(client)
        workspace_ids.append(workspace_id)
        source_id = create_search_evidence(client, workspace_id)
        source_before = client.get(
            f"/v1/market/workspaces/{workspace_id}/evidence",
            headers=headers(),
        )
        assert source_before.status_code == 200, source_before.text
        original = next(
            item for item in source_before.json()["items"] if item["id"] == source_id
        )
        original_hash = original["content_hash"]

        request_key = str(uuid.uuid4())
        queued = client.post(
            f"/v1/market/workspaces/{workspace_id}/evidence/{source_id}:extract",
            headers=headers(request_key),
        )
        assert queued.status_code == 202, queued.text
        accepted = queued.json()
        assert accepted["status"] == "PENDING"
        assert accepted["reused"] is False
        workflow_ids.append(accepted["workflow_id"])
        job_ids.append(accepted["job_id"])

        replay = client.post(
            f"/v1/market/workspaces/{workspace_id}/evidence/{source_id}:extract",
            headers=headers(request_key),
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["workflow_id"] == accepted["workflow_id"]

        # The only external fetch is the specific URL from the user-selected Evidence.
        result = execute_job(accepted["job_id"])
        assert provider.requested == ["https://example.com/research"]
        assert result["verification_status"] == "UNVERIFIED"

        with org_session(UUID(ORG_A)) as session:
            extracted = session.scalars(
                select(Evidence).where(
                    Evidence.organization_id == UUID(ORG_A),
                    Evidence.subject_type == "market_workspace",
                    Evidence.subject_id == UUID(workspace_id),
                    Evidence.kind == "WEB_PAGE_EXTRACT",
                )
            ).one()
            assert extracted.verification_status == "UNVERIFIED"
            assert extracted.canonical_ref == "https://example.com/research/article"
            assert extracted.content_hash == provider.page.content_hash
            assert extracted.payload["content"] == provider.page.excerpt
            assert extracted.payload["content_hash_scope"] == "normalized_full_extracted_text"
            assert extracted.payload["derived_from_evidence_id"] == source_id

            source_after = session.get(Evidence, UUID(source_id))
            assert source_after is not None
            assert source_after.content_hash == original_hash
            assert source_after.kind == "WEB_SEARCH_RESULT"

            lineage = session.scalars(
                select(MarketEvidenceDerivation).where(
                    MarketEvidenceDerivation.organization_id == UUID(ORG_A),
                    MarketEvidenceDerivation.workspace_id == UUID(workspace_id),
                    MarketEvidenceDerivation.source_evidence_id == UUID(source_id),
                    MarketEvidenceDerivation.derived_evidence_id == extracted.id,
                )
            ).one()
            assert lineage.workflow_id == UUID(accepted["workflow_id"])
            assert lineage.content_hash == provider.page.content_hash

        status = client.get(
            f"/v1/market/workspaces/{workspace_id}/source-extractions/{accepted['workflow_id']}",
            headers=headers(),
        )
        assert status.status_code == 200, status.text
        assert status.json()["extraction_status"] == "COMPLETED"
        assert status.json()["extracted_evidence_id"] == result["extracted_evidence_id"]
    finally:
        cleanup(workspace_ids, workflow_ids, job_ids)


def test_non_search_evidence_cannot_be_submitted_for_extraction(
    client: TestClient,
) -> None:
    workspace_ids: list[str] = []
    try:
        workspace_id = create_workspace(client)
        workspace_ids.append(workspace_id)
        response = client.post(
            f"/v1/market/workspaces/{workspace_id}/evidence",
            headers=headers(),
            json={
                "canonical_ref": "https://example.com/manual",
                "content": "A user-authored note that is not a search result.",
                "kind": "USER_NOTE",
            },
        )
        assert response.status_code == 201, response.text
        evidence_id = response.json()["id"]

        rejected = client.post(
            f"/v1/market/workspaces/{workspace_id}/evidence/{evidence_id}:extract",
            headers=headers(),
        )
        assert rejected.status_code == 422, rejected.text
    finally:
        cleanup(workspace_ids, [], [])
