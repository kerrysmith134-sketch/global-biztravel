"""通用、用户意图驱动的研究计划 API 验收。"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from app.db import org_session
from app.integrations.research_search import ResearchSearchResult
from app.main import app
from app.models.market import MarketActivityEvent, MarketResearchPlanEvidence, MarketWorkspace
from app.models.reliability import BackgroundJob
from app.models.workflow import AgentRun, Evidence, WorkflowInstance
from app.services import job_service
from app.workers.tasks import handle_research_execute
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
USER_A = "22222222-2222-2222-2222-222222222222"
USER_B = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(org: str = ORG_A, user: str = USER_A, key: str | None = None) -> dict[str, str]:
    return {
        "X-Org-Id": org,
        "X-User-Id": user,
        "Idempotency-Key": key or str(uuid.uuid4()),
    }


def test_research_plan_is_user_intent_driven_and_does_not_fake_retrieval(client: TestClient) -> None:
    idempotency_key = str(uuid.uuid4())
    payload = {
        "request": "研究 ACME GmbH 的企业背景，并为下周拜访准备 8 个问题",
        "source_scope": "EXTERNAL_ONLY",
        "trip_id": "55555555-5555-5555-5555-555555555555",
        "customer_id": "66666666-6666-6666-6666-666666666666",
        "context_text": "用户补充：希望关注对方近期业务变化。",
    }
    created = client.post(
        "/v1/research/plans",
        headers=headers(key=idempotency_key),
        json=payload,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    plan_id = body["id"]

    assert body["status"] == "PLANNED"
    assert body["retrieval_status"] == "NOT_STARTED"
    assert body["requested_scope"] == "EXTERNAL_ONLY"
    assert body["recommended_scope"] == "EXTERNAL_ONLY"
    assert body["trip_id"] is None
    assert body["customer_id"] is None
    assert body["plan"]["subject"]
    assert body["plan"]["search_queries"]
    assert body["plan"]["external_research_needed"] is True
    # The offline provider has no industry defaults and no fabricated market facts.
    assert body["plan"]["domain"] is None
    assert body["plan"]["geographies"] == []
    assert body["plan"]["entities"] == []
    evidence_kinds = {item["kind"] for item in body["evidence"]}
    assert evidence_kinds == {"USER_REQUEST", "USER_PROVIDED_CONTEXT"}
    assert not any(item["kind"] == "EXTERNAL_SOURCE" for item in body["evidence"])

    # Same idempotency key replays the same plan without creating another workflow/run.
    replay = client.post(
        "/v1/research/plans",
        headers=headers(key=idempotency_key),
        json=payload,
    )
    assert replay.status_code == 201
    assert replay.headers.get("Idempotent-Replay") == "true"
    assert replay.json()["id"] == plan_id

    detail = client.get(f"/v1/research/plans/{plan_id}", headers=headers())
    assert detail.status_code == 200
    assert detail.json()["id"] == plan_id
    assert detail.json()["planner"]["llm_invocation_id"]

    with org_session(UUID(ORG_A)) as session:
        workflows = session.scalars(
            select(WorkflowInstance).where(
                WorkflowInstance.id == UUID(plan_id),
                WorkflowInstance.workflow_type == "research_plan",
            )
        ).all()
        runs = session.scalars(
            select(AgentRun).where(AgentRun.workflow_id == UUID(plan_id))
        ).all()
        evidence = session.scalars(
            select(Evidence).where(
                Evidence.subject_type == "RESEARCH_PLAN",
                Evidence.subject_id == UUID(plan_id),
            )
        ).all()
        assert len(workflows) == 1
        assert len(runs) == 1
        assert "user_provided_context" not in runs[0].input
        assert runs[0].input["user_provided_context_sha256"]
        assert {row.kind for row in evidence} == {"USER_REQUEST", "USER_PROVIDED_CONTEXT"}
        assert {row.source for row in evidence} == {"user_input"}
        assert all("sha256" in row.payload for row in evidence)
        assert all("payload" not in row.payload for row in evidence)

    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Evidence).where(Evidence.subject_id == UUID(plan_id)))
        session.execute(delete(AgentRun).where(AgentRun.workflow_id == UUID(plan_id)))
        session.execute(delete(WorkflowInstance).where(WorkflowInstance.id == UUID(plan_id)))


def test_research_plan_internal_only_has_no_external_queries(client: TestClient) -> None:
    created = client.post(
        "/v1/research/plans",
        headers=headers(),
        json={
            "request": "整理这次行程的现有拜访记录，列出尚未确认的事项",
            "source_scope": "INTERNAL_ONLY",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["recommended_scope"] == "INTERNAL_ONLY"
    assert body["plan"]["search_queries"] == []
    assert body["plan"]["external_research_needed"] is False
    plan_id = body["id"]
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Evidence).where(Evidence.subject_id == UUID(plan_id)))
        session.execute(delete(AgentRun).where(AgentRun.workflow_id == UUID(plan_id)))
        session.execute(delete(WorkflowInstance).where(WorkflowInstance.id == UUID(plan_id)))


def test_auto_scope_does_not_mistake_user_material_for_internal_records(client: TestClient) -> None:
    created = client.post(
        "/v1/research/plans",
        headers=headers(),
        json={
            "request": "研究 ACME GmbH 的近期业务背景",
            "source_scope": "AUTO",
            "context_text": "用户粘贴的公司介绍材料",
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    plan_id = body["id"]
    assert body["recommended_scope"] == "EXTERNAL_ONLY"
    assert "用户主动提供的材料" in body["plan"]["source_types"]
    assert body["plan"]["external_research_needed"] is True
    assert {item["kind"] for item in body["evidence"]} == {
        "USER_REQUEST",
        "USER_PROVIDED_CONTEXT",
    }
    assert not any(item["kind"] == "INTERNAL_CUSTOMER_CONTEXT" for item in body["evidence"])
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Evidence).where(Evidence.subject_id == UUID(plan_id)))
        session.execute(delete(AgentRun).where(AgentRun.workflow_id == UUID(plan_id)))
        session.execute(delete(WorkflowInstance).where(WorkflowInstance.id == UUID(plan_id)))


def test_research_execution_uses_reviewed_queries_and_persists_unverified_sources(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    executed_queries: list[str] = []

    class FakeProvider:
        name = "test-search"
        configured = True

        def search(self, query: str, *, language: str | None = None):
            executed_queries.append(query)
            return [
                ResearchSearchResult(
                    url="https://source.example.test/company",
                    title="Company profile",
                    snippet=f"Search snippet for: {query}",
                    provider=self.name,
                    engine="fixture",
                    published_at="2026-11-20",
                )
            ]

    provider = FakeProvider()
    monkeypatch.setattr(
        "app.services.research_service.get_research_search_provider", lambda: provider
    )
    monkeypatch.setattr(
        "app.workers.tasks.get_research_search_provider", lambda: provider
    )

    created = client.post(
        "/v1/research/plans",
        headers=headers(),
        json={
            "request": "Research the public background of Example Holdings and prepare visit questions",
            "source_scope": "EXTERNAL_ONLY",
        },
    )
    assert created.status_code == 201, created.text
    plan_id = created.json()["id"]

    execution_key = str(uuid.uuid4())
    confirmed_queries = ["reviewed query one", "reviewed query two"]
    accepted = client.post(
        f"/v1/research/plans/{plan_id}:execute",
        headers=headers(key=execution_key),
        json={"search_queries": confirmed_queries},
    )
    assert accepted.status_code == 202, accepted.text
    accepted_body = accepted.json()
    assert accepted_body["status"] == "PENDING"
    job_id = accepted_body["job_id"]

    replay = client.post(
        f"/v1/research/plans/{plan_id}:execute",
        headers=headers(key=execution_key),
        json={"search_queries": confirmed_queries},
    )
    # A different key is a second idempotent request, but the in-flight dedupe key reuses
    # the same job instead of creating a second search task.
    assert replay.status_code == 202, replay.text
    assert replay.headers.get("Idempotent-Replay") == "true"
    assert replay.json()["job_id"] == job_id
    queued = client.get(f"/v1/research/plans/{plan_id}", headers=headers())
    assert queued.status_code == 200
    assert queued.json()["retrieval_status"] == "QUEUED"
    assert queued.json()["job_id"] == job_id
    assert queued.json()["reviewed_search_queries"] == confirmed_queries

    with org_session(UUID(ORG_A)) as session:
        job = session.get(BackgroundJob, UUID(job_id))
        assert job is not None
        job.status = "RUNNING"
        job.locked_by = "research-test-worker"
        result = handle_research_execute(session, job)
        job_service.complete(session, job.id, result=result)
        assert result["retrieval_status"] == "COMPLETED"
        assert result["results_count"] == 1
        assert executed_queries == confirmed_queries
        evidence = session.scalars(
            select(Evidence).where(
                Evidence.organization_id == UUID(ORG_A),
                Evidence.subject_type == "RESEARCH_PLAN",
                Evidence.subject_id == UUID(plan_id),
                Evidence.kind == "WEB_SEARCH_RESULT",
            )
        ).all()
        assert len(evidence) == 1
        assert evidence[0].source == "test-search"
        assert evidence[0].ref == "https://source.example.test/company"
        assert evidence[0].payload["evidence_state"] == "UNVERIFIED_SEARCH_RESULT"

    completed = client.get(f"/v1/research/plans/{plan_id}", headers=headers())
    assert completed.status_code == 200
    assert completed.json()["retrieval_status"] == "COMPLETED"
    assert completed.json()["results_count"] == 1
    assert completed.json()["reviewed_search_queries"] == confirmed_queries
    assert any(item["kind"] == "WEB_SEARCH_RESULT" for item in completed.json()["evidence"])

    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Evidence).where(Evidence.subject_id == UUID(plan_id)))
        session.execute(delete(AgentRun).where(AgentRun.workflow_id == UUID(plan_id)))
        session.execute(delete(WorkflowInstance).where(WorkflowInstance.id == UUID(plan_id)))
        session.execute(delete(BackgroundJob).where(BackgroundJob.id == UUID(job_id)))


def test_research_execution_fails_closed_when_provider_is_not_configured(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class MissingProvider:
        configured = False
        name = "unavailable"

    monkeypatch.setattr(
        "app.services.research_service.get_research_search_provider",
        lambda: MissingProvider(),
    )
    created = client.post(
        "/v1/research/plans",
        headers=headers(),
        json={
            "request": "Research the public background of Example Holdings",
            "source_scope": "EXTERNAL_ONLY",
        },
    )
    assert created.status_code == 201, created.text
    plan_id = created.json()["id"]
    denied = client.post(
        f"/v1/research/plans/{plan_id}:execute",
        headers=headers(),
        json={"search_queries": ["one reviewed query"]},
    )
    assert denied.status_code == 503, denied.text
    detail = client.get(f"/v1/research/plans/{plan_id}", headers=headers())
    assert detail.status_code == 200
    assert detail.json()["retrieval_status"] == "NOT_STARTED"
    assert detail.json()["job_id"] is None
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Evidence).where(Evidence.subject_id == UUID(plan_id)))
        session.execute(delete(AgentRun).where(AgentRun.workflow_id == UUID(plan_id)))
        session.execute(delete(WorkflowInstance).where(WorkflowInstance.id == UUID(plan_id)))


def test_research_execute_rejects_empty_reviewed_queries(client: TestClient) -> None:
    created = client.post(
        "/v1/research/plans",
        headers=headers(),
        json={
            "request": "Research the public background of Example Holdings",
            "source_scope": "EXTERNAL_ONLY",
        },
    )
    assert created.status_code == 201, created.text
    plan_id = created.json()["id"]
    invalid = client.post(
        f"/v1/research/plans/{plan_id}:execute",
        headers=headers(),
        json={"search_queries": [" ", ""]},
    )
    assert invalid.status_code == 400, invalid.text
    detail = client.get(f"/v1/research/plans/{plan_id}", headers=headers())
    assert detail.json()["retrieval_status"] == "NOT_STARTED"
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Evidence).where(Evidence.subject_id == UUID(plan_id)))
        session.execute(delete(AgentRun).where(AgentRun.workflow_id == UUID(plan_id)))
        session.execute(delete(WorkflowInstance).where(WorkflowInstance.id == UUID(plan_id)))


def test_research_plan_is_tenant_scoped(client: TestClient) -> None:
    created = client.post(
        "/v1/research/plans",
        headers=headers(),
        json={"request": "Research the publicly available background of a named company"},
    )
    assert created.status_code == 201, created.text
    plan_id = created.json()["id"]
    hidden = client.get(
        f"/v1/research/plans/{plan_id}",
        headers=headers(ORG_B, USER_B),
    )
    assert hidden.status_code == 404
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Evidence).where(Evidence.subject_id == UUID(plan_id)))
        session.execute(delete(AgentRun).where(AgentRun.workflow_id == UUID(plan_id)))
        session.execute(delete(WorkflowInstance).where(WorkflowInstance.id == UUID(plan_id)))


def test_workspace_research_worker_uses_shared_versioned_evidence_and_plan_query_links(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    executed_queries: list[str] = []

    class FakeProvider:
        name = "workspace-test-search"
        configured = True
        snippet = "First captured source version: buyer demand is increasing."

        def search(self, query: str, *, language: str | None = None):
            executed_queries.append(query)
            return [
                ResearchSearchResult(
                    url="https://source.example.test/market-report#overview",
                    title="Market report",
                    snippet=self.snippet,
                    provider=self.name,
                    engine="fixture",
                    published_at="2026-11-20",
                )
            ]

    provider = FakeProvider()
    monkeypatch.setattr(
        "app.services.research_service.get_research_search_provider", lambda: provider
    )
    monkeypatch.setattr(
        "app.workers.tasks.get_research_search_provider", lambda: provider
    )

    workspace_response = client.post(
        "/v1/market/workspaces",
        headers=headers(),
        json={
            "name": f"Versioned research evidence {uuid.uuid4().hex[:8]}",
            "objective": "Verify source versioning across ResearchPlans",
        },
    )
    assert workspace_response.status_code == 201, workspace_response.text
    workspace_id = workspace_response.json()["id"]
    plan_ids: list[str] = []
    job_ids: list[str] = []

    def execute_plan(query: str) -> tuple[str, dict]:
        created = client.post(
            "/v1/research/plans",
            headers=headers(),
            json={
                "request": "Research public market evidence for the topic explicitly supplied by the user.",
                "source_scope": "EXTERNAL_ONLY",
                "workspace_id": workspace_id,
            },
        )
        assert created.status_code == 201, created.text
        plan_id = created.json()["id"]
        plan_ids.append(plan_id)

        queued = client.post(
            f"/v1/research/plans/{plan_id}:execute",
            headers=headers(),
            json={"search_queries": [query]},
        )
        assert queued.status_code == 202, queued.text
        job_id = queued.json()["job_id"]
        job_ids.append(job_id)

        with org_session(UUID(ORG_A)) as session:
            job = session.get(BackgroundJob, UUID(job_id))
            assert job is not None
            job.status = "RUNNING"
            job.locked_by = "workspace-research-test-worker"
            result = handle_research_execute(session, job)
            job_service.complete(session, job.id, result=result)
            assert result["retrieval_status"] == "COMPLETED"
            assert result["evidence_storage_mode"] == "WORKSPACE_VERSIONED"
            assert result["results_count"] == 1

        detail = client.get(f"/v1/research/plans/{plan_id}", headers=headers())
        assert detail.status_code == 200, detail.text
        assert detail.json()["retrieval_status"] == "COMPLETED"
        assert detail.json()["results_count"] == 1
        research_evidence = [
            item for item in detail.json()["evidence"] if item["kind"] == "WEB_SEARCH_RESULT"
        ]
        assert len(research_evidence) == 1
        return plan_id, research_evidence[0]

    try:
        first_plan_id, first_result = execute_plan("user-reviewed demand query")
        provider.snippet = "First captured source version: buyer demand is increasing."
        second_plan_id, second_result = execute_plan("user-reviewed competitor query")

        # Same canonical source + same content is one shared Evidence row across plans.
        assert first_result["id"] == second_result["id"]
        assert first_result["payload"]["content"] == (
            "Market report\n\nFirst captured source version: buyer demand is increasing."
        )

        # Changed source content at the same URL creates a second immutable Evidence version.
        provider.snippet = "Updated captured source version: procurement plans were paused."
        third_plan_id, third_result = execute_plan("user-reviewed update query")
        assert third_result["id"] != first_result["id"]

        with org_session(UUID(ORG_A)) as session:
            source_rows = session.scalars(
                select(Evidence).where(
                    Evidence.organization_id == UUID(ORG_A),
                    Evidence.subject_type == "market_workspace",
                    Evidence.subject_id == UUID(workspace_id),
                    Evidence.kind == "WEB_SEARCH_RESULT",
                )
            ).all()
            assert len(source_rows) == 2
            assert len({row.content_hash for row in source_rows}) == 2
            assert all(row.verification_status == "UNVERIFIED" for row in source_rows)
            assert all(row.quality is None for row in source_rows)
            assert all(row.canonical_ref == "https://source.example.test/market-report" for row in source_rows)

            links = session.scalars(
                select(MarketResearchPlanEvidence).where(
                    MarketResearchPlanEvidence.organization_id == UUID(ORG_A),
                    MarketResearchPlanEvidence.workspace_id == UUID(workspace_id),
                    MarketResearchPlanEvidence.research_plan_id.in_(
                        [UUID(first_plan_id), UUID(second_plan_id), UUID(third_plan_id)]
                    ),
                )
            ).all()
            assert len(links) == 3
            evidence_by_plan = {str(link.research_plan_id): str(link.evidence_id) for link in links}
            assert evidence_by_plan[first_plan_id] == evidence_by_plan[second_plan_id]
            assert evidence_by_plan[third_plan_id] != evidence_by_plan[first_plan_id]
            assert all(len(link.query_sha256) == 64 for link in links)
            assert all(link.collected_by_run_id is not None for link in links)

            recorded_events = session.scalars(
                select(MarketActivityEvent).where(
                    MarketActivityEvent.organization_id == UUID(ORG_A),
                    MarketActivityEvent.workspace_id == UUID(workspace_id),
                    MarketActivityEvent.event_type == "MARKET_EVIDENCE_RECORDED",
                )
            ).all()
            linked_events = session.scalars(
                select(MarketActivityEvent).where(
                    MarketActivityEvent.organization_id == UUID(ORG_A),
                    MarketActivityEvent.workspace_id == UUID(workspace_id),
                    MarketActivityEvent.event_type == "MARKET_RESEARCH_EVIDENCE_LINKED",
                )
            ).all()
            # The second Plan reuses the same source body; only two source versions are captured.
            assert len(recorded_events) == 2
            assert len(linked_events) == 3

        workspace_evidence = client.get(
            f"/v1/market/workspaces/{workspace_id}/evidence",
            headers=headers(),
        )
        assert workspace_evidence.status_code == 200, workspace_evidence.text
        assert len(workspace_evidence.json()["items"]) == 2
        assert executed_queries == [
            "user-reviewed demand query",
            "user-reviewed competitor query",
            "user-reviewed update query",
        ]
    finally:
        with org_session(UUID(ORG_A)) as session:
            # Workspace deletion cascades the ResearchPlan/Evidence relationship rows.
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
            if plan_ids:
                session.execute(
                    delete(Evidence).where(
                        Evidence.organization_id == UUID(ORG_A),
                        Evidence.subject_type == "RESEARCH_PLAN",
                        Evidence.subject_id.in_([UUID(item) for item in plan_ids]),
                    )
                )
                session.execute(
                    delete(AgentRun).where(
                        AgentRun.organization_id == UUID(ORG_A),
                        AgentRun.workflow_id.in_([UUID(item) for item in plan_ids]),
                    )
                )
                session.execute(
                    delete(WorkflowInstance).where(
                        WorkflowInstance.organization_id == UUID(ORG_A),
                        WorkflowInstance.id.in_([UUID(item) for item in plan_ids]),
                    )
                )
            if job_ids:
                session.execute(
                    delete(BackgroundJob).where(
                        BackgroundJob.organization_id == UUID(ORG_A),
                        BackgroundJob.id.in_([UUID(item) for item in job_ids]),
                    )
                )
