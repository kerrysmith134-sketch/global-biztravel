"""Market Workspace / Context Snapshot / Research Question 的 PostgreSQL 集成验收。"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.geo import Customer
from app.models.market import (
    MarketActivityEvent,
    MarketContextSnapshot,
    MarketEntity,
    MarketEntityAlias,
    MarketEntityCustomerLink,
    MarketResearchQuestion,
    MarketWorkspace,
    MarketWorkspaceEntity,
    MarketWorkspaceWorkflow,
)
from app.models.workflow import AgentRun, Evidence, WorkflowInstance
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


def headers(
    user: str = ADMIN_A,
    org: str = ORG_A,
    *,
    key: str | None = None,
) -> dict[str, str]:
    return {
        "X-Org-Id": org,
        "X-User-Id": user,
        "Idempotency-Key": key or str(uuid.uuid4()),
    }


def create_workspace(client: TestClient, name: str) -> dict:
    response = client.post(
        "/v1/market/workspaces",
        headers=headers(),
        json={"name": name, "objective": "验证通用市场发现流程"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_workspace_context_and_question_lifecycle(client: TestClient) -> None:
    workspace_id: str | None = None
    other_workspace_id: str | None = None
    key = str(uuid.uuid4())
    payload = {
        "name": f"市场探索·{uuid.uuid4().hex[:8]}",
        "objective": "研究新市场和潜在合作机会",
        "initial_context": "我们提供企业服务，希望研究新的目标市场。",
    }
    try:
        first = client.post(
            "/v1/market/workspaces", headers=headers(key=key), json=payload
        )
        assert first.status_code == 201, first.text
        workspace_id = first.json()["id"]
        assert first.json()["current_context_version"] == 1

        # 相同 Idempotency-Key + 相同请求体必须回放首次响应，不产生第二个 Workspace。
        replay = client.post(
            "/v1/market/workspaces", headers=headers(key=key), json=payload
        )
        assert replay.status_code == 201, replay.text
        assert replay.json()["id"] == workspace_id
        assert replay.headers.get("Idempotent-Replay") == "true"

        contexts = client.get(
            f"/v1/market/workspaces/{workspace_id}/contexts", headers=headers()
        )
        assert contexts.status_code == 200, contexts.text
        assert len(contexts.json()) == 1
        assert contexts.json()[0]["raw_input"] == payload["initial_context"]
        assert contexts.json()[0]["is_current"] is True
        assert contexts.json()[0]["version"] == 1

        # 创建新版本但暂不采纳，不得改变当前上下文。
        draft = client.post(
            f"/v1/market/workspaces/{workspace_id}/contexts",
            headers=headers(),
            json={"raw_input": "补充：优先研究渠道合作伙伴。", "adopt": False},
        )
        assert draft.status_code == 201, draft.text
        draft_id = draft.json()["id"]
        assert draft.json()["version"] == 2
        assert draft.json()["is_current"] is False

        detail = client.get(
            f"/v1/market/workspaces/{workspace_id}", headers=headers()
        )
        assert detail.status_code == 200
        assert detail.json()["current_context_version"] == 1

        adopted = client.post(
            f"/v1/market/workspaces/{workspace_id}/contexts/{draft_id}:adopt",
            headers=headers(),
        )
        assert adopted.status_code == 200, adopted.text
        assert adopted.json()["is_current"] is True
        assert adopted.json()["version"] == 2

        contexts_after = client.get(
            f"/v1/market/workspaces/{workspace_id}/contexts", headers=headers()
        )
        assert contexts_after.status_code == 200
        assert sum(1 for row in contexts_after.json() if row["is_current"]) == 1

        detail_after = client.get(
            f"/v1/market/workspaces/{workspace_id}", headers=headers()
        )
        assert detail_after.json()["current_context_version"] == 2

        root = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions",
            headers=headers(),
            json={
                "question": "目标客户通常通过什么渠道采购？",
                "priority": 1,
                "acceptance_criteria": [{"kind": "independent_sources", "minimum": 2}],
            },
        )
        assert root.status_code == 201, root.text
        root_id = root.json()["id"]
        assert root.json()["source"] == "USER"
        assert root.json()["status"] == "OPEN"

        child = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions",
            headers=headers(),
            json={
                "question": "哪些行业存在明确的采购触发因素？",
                "parent_question_id": root_id,
                "priority": 2,
            },
        )
        assert child.status_code == 201, child.text
        assert child.json()["parent_question_id"] == root_id

        other = create_workspace(client, f"另一个工作空间·{uuid.uuid4().hex[:6]}")
        other_workspace_id = other["id"]
        other_question = client.post(
            f"/v1/market/workspaces/{other_workspace_id}/questions",
            headers=headers(),
            json={"question": "另一个工作空间的问题"},
        )
        assert other_question.status_code == 201, other_question.text

        # 父问题必须属于同一个 Workspace，不能通过有效 UUID 形成跨 Workspace 引用。
        invalid_parent = client.post(
            f"/v1/market/workspaces/{workspace_id}/questions",
            headers=headers(),
            json={
                "question": "不能引用其他工作空间的父问题",
                "parent_question_id": other_question.json()["id"],
            },
        )
        assert invalid_parent.status_code == 404, invalid_parent.text

        questions = client.get(
            f"/v1/market/workspaces/{workspace_id}/questions", headers=headers()
        )
        assert questions.status_code == 200, questions.text
        assert len(questions.json()["items"]) == 2

        activity = client.get(
            f"/v1/market/workspaces/{workspace_id}/activity", headers=headers()
        )
        assert activity.status_code == 200, activity.text
        event_types = {item["event_type"] for item in activity.json()}
        assert "WORKSPACE_CREATED" in event_types
        assert "CONTEXT_SNAPSHOT_ADOPTED" in event_types
        assert "RESEARCH_QUESTION_CREATED" in event_types

        with org_session(UUID(ORG_A)) as session:
            assert session.scalar(
                select(func.count()).select_from(MarketWorkspace).where(
                    MarketWorkspace.id == UUID(workspace_id)
                )
            ) == 1
            assert session.scalar(
                select(func.count()).select_from(MarketContextSnapshot).where(
                    MarketContextSnapshot.organization_id == UUID(ORG_A),
                    MarketContextSnapshot.workspace_id == UUID(workspace_id),
                    MarketContextSnapshot.is_current.is_(True),
                )
            ) == 1
            assert session.scalar(
                select(func.count()).select_from(MarketResearchQuestion).where(
                    MarketResearchQuestion.organization_id == UUID(ORG_A),
                    MarketResearchQuestion.workspace_id == UUID(workspace_id),
                )
            ) == 2
            assert session.scalar(
                select(func.count()).select_from(MarketActivityEvent).where(
                    MarketActivityEvent.organization_id == UUID(ORG_A),
                    MarketActivityEvent.workspace_id == UUID(workspace_id),
                )
            ) >= 5

    finally:
        with org_session(UUID(ORG_A)) as session:
            ids = [item for item in (workspace_id, other_workspace_id) if item is not None]
            if ids:
                session.execute(
                    delete(MarketWorkspace).where(MarketWorkspace.id.in_([UUID(item) for item in ids]))
                )


def test_market_workspace_is_tenant_isolated(client: TestClient) -> None:
    workspace_id: str | None = None
    try:
        workspace = create_workspace(client, f"租户隔离·{uuid.uuid4().hex[:8]}")
        workspace_id = workspace["id"]

        stolen_detail = client.get(
            f"/v1/market/workspaces/{workspace_id}",
            headers=headers(USER_B, ORG_B),
        )
        assert stolen_detail.status_code == 404

        stolen_contexts = client.get(
            f"/v1/market/workspaces/{workspace_id}/contexts",
            headers=headers(USER_B, ORG_B),
        )
        assert stolen_contexts.status_code == 404

        stolen_questions = client.get(
            f"/v1/market/workspaces/{workspace_id}/questions",
            headers=headers(USER_B, ORG_B),
        )
        assert stolen_questions.status_code == 404

        # 直接使用受 RLS 约束的 gbt_app 会话读取：即使绕过 API 显式过滤也不可见。
        with org_session(UUID(ORG_B)) as session:
            assert session.scalar(
                select(func.count()).select_from(MarketWorkspace).where(
                    MarketWorkspace.id == UUID(workspace_id)
                )
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(MarketContextSnapshot).where(
                    MarketContextSnapshot.workspace_id == UUID(workspace_id)
                )
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(MarketResearchQuestion).where(
                    MarketResearchQuestion.workspace_id == UUID(workspace_id)
                )
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(MarketActivityEvent).where(
                    MarketActivityEvent.workspace_id == UUID(workspace_id)
                )
            ) == 0
    finally:
        if workspace_id is not None:
            with org_session(UUID(ORG_A)) as session:
                session.execute(
                    delete(MarketWorkspace).where(MarketWorkspace.id == UUID(workspace_id))
                )



def test_workspace_contains_existing_research_plan_workflows(client: TestClient) -> None:
    workspace_id: str | None = None
    workflow_id: str | None = None
    try:
        workspace = create_workspace(client, f"长期市场研究·{uuid.uuid4().hex[:8]}")
        workspace_id = workspace["id"]

        # A standalone, user-intent-driven research plan may be attached to a workspace.
        # EXTERNAL_ONLY must still avoid reading selected Trip/Customer data.
        plan_response = client.post(
            "/v1/research/plans",
            headers=headers(),
            json={
                "request": "Research publicly available market entry requirements for a user-selected target market",
                "source_scope": "EXTERNAL_ONLY",
                "trip_id": "55555555-5555-5555-5555-555555555555",
                "customer_id": "66666666-6666-6666-6666-666666666666",
                "workspace_id": workspace_id,
            },
        )
        assert plan_response.status_code == 201, plan_response.text
        plan = plan_response.json()
        workflow_id = plan["id"]
        assert plan["workspace_id"] == workspace_id
        assert plan["trip_id"] is None
        assert plan["customer_id"] is None
        assert plan["retrieval_status"] == "NOT_STARTED"

        # The workspace lists existing ResearchPlan workflows rather than creating
        # a second research-plan table or another worker subsystem.
        listed = client.get(
            f"/v1/market/workspaces/{workspace_id}/research-plans",
            headers=headers(),
        )
        assert listed.status_code == 200, listed.text
        assert [item["id"] for item in listed.json()] == [workflow_id]
        assert listed.json()[0]["workspace_id"] == workspace_id

        with org_session(UUID(ORG_A)) as session:
            link = session.scalars(
                select(MarketWorkspaceWorkflow).where(
                    MarketWorkspaceWorkflow.organization_id == UUID(ORG_A),
                    MarketWorkspaceWorkflow.workspace_id == UUID(workspace_id),
                    MarketWorkspaceWorkflow.workflow_id == UUID(workflow_id),
                )
            ).one_or_none()
            assert link is not None
            workflow = session.scalars(
                select(WorkflowInstance).where(
                    WorkflowInstance.organization_id == UUID(ORG_A),
                    WorkflowInstance.id == UUID(workflow_id),
                    WorkflowInstance.workflow_type == "research_plan",
                )
            ).one_or_none()
            assert workflow is not None
            assert workflow.payload["workspace_id"] == workspace_id
            events = session.scalars(
                select(MarketActivityEvent).where(
                    MarketActivityEvent.organization_id == UUID(ORG_A),
                    MarketActivityEvent.workspace_id == UUID(workspace_id),
                    MarketActivityEvent.event_type == "RESEARCH_PLAN_LINKED",
                )
            ).all()
            assert len(events) == 1

        # Both the API and the underlying RLS policy hide the association from another tenant.
        hidden = client.get(
            f"/v1/market/workspaces/{workspace_id}/research-plans",
            headers=headers(USER_B, ORG_B),
        )
        assert hidden.status_code == 404
        with org_session(UUID(ORG_B)) as session:
            assert session.scalar(
                select(func.count()).select_from(MarketWorkspaceWorkflow).where(
                    MarketWorkspaceWorkflow.workspace_id == UUID(workspace_id)
                )
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(WorkflowInstance).where(
                    WorkflowInstance.id == UUID(workflow_id)
                )
            ) == 0

    finally:
        if workflow_id is not None:
            with org_session(UUID(ORG_A)) as session:
                session.execute(delete(Evidence).where(Evidence.subject_id == UUID(workflow_id)))
                session.execute(delete(AgentRun).where(AgentRun.workflow_id == UUID(workflow_id)))
                session.execute(delete(WorkflowInstance).where(WorkflowInstance.id == UUID(workflow_id)))
        if workspace_id is not None:
            with org_session(UUID(ORG_A)) as session:
                session.execute(
                    delete(MarketWorkspace).where(MarketWorkspace.id == UUID(workspace_id))
                )



def test_market_entity_is_candidate_and_customer_link_requires_explicit_confirmation(
    client: TestClient,
) -> None:
    workspace_id: str | None = None
    other_workspace_id: str | None = None
    entity_id: str | None = None
    customer_a_id = uuid.uuid4()
    customer_b_id = uuid.uuid4()
    try:
        workspace = create_workspace(client, f"Entity discovery·{uuid.uuid4().hex[:8]}")
        workspace_id = workspace["id"]
        other_workspace = create_workspace(client, f"Entity reuse·{uuid.uuid4().hex[:8]}")
        other_workspace_id = other_workspace["id"]

        with org_session(UUID(ORG_A)) as session:
            session.add(
                Customer(
                    id=customer_a_id,
                    organization_id=UUID(ORG_A),
                    name="Existing CRM Customer",
                    tier="B",
                    country_code="KE",
                    industry="Existing industry",
                    status="ACTIVE",
                    notes="Keep this manually maintained customer record unchanged.",
                )
            )
        with org_session(UUID(ORG_B)) as session:
            session.add(
                Customer(
                    id=customer_b_id,
                    organization_id=UUID(ORG_B),
                    name="Tenant B Customer",
                    tier="C",
                    country_code="KE",
                    status="PROSPECT",
                )
            )

        before_count = None
        with org_session(UUID(ORG_A)) as session:
            before_count = session.scalar(
                select(func.count()).select_from(Customer).where(
                    Customer.organization_id == UUID(ORG_A)
                )
            )

        created = client.post(
            f"/v1/market/workspaces/{workspace_id}/entities",
            headers=headers(),
            json={
                "entity_kind": "Distributor / Channel Partner",
                "display_name": "Example East Africa Group",
                "description": "Candidate found during a user-directed market exploration.",
                "canonical_domain": "https://www.example.test/company/",
                "country_code": "ke",
                "attributes": {"employee_band": "UNKNOWN", "custom_taxonomy": "channel"},
                "discovery_reason": "Mentioned in the user's market brief.",
                "relevance_note": "Potential partner; demand is not yet verified.",
            },
        )
        assert created.status_code == 201, created.text
        entity_body = created.json()["entity"]
        entity_id = entity_body["id"]
        assert entity_body["status"] == "CANDIDATE"
        assert entity_body["entity_kind"] == "Distributor / Channel Partner"
        assert entity_body["canonical_domain"] == "example.test"
        assert {item["alias_type"] for item in entity_body["aliases"]} == {"NAME", "DOMAIN"}
        assert created.json()["possible_duplicate_entity_ids"] == []

        # A shared canonical domain is only a duplicate signal, never an automatic merge.
        same_domain = client.post(
            f"/v1/market/workspaces/{other_workspace_id}/entities",
            headers=headers(),
            json={
                "entity_kind": "Organization",
                "display_name": "Example East Africa Group - alternate label",
                "canonical_domain": "example.test",
                "country_code": "KE",
            },
        )
        assert same_domain.status_code == 201, same_domain.text
        assert same_domain.json()["entity"]["id"] != entity_id
        assert entity_id in same_domain.json()["possible_duplicate_entity_ids"]

        # Reuse across Workspaces is an explicit action by exact entity ID.
        attached = client.post(
            f"/v1/market/workspaces/{other_workspace_id}/entities",
            headers=headers(),
            json={
                "existing_entity_id": entity_id,
                "discovery_reason": "The user explicitly selected the existing candidate.",
            },
        )
        assert attached.status_code == 201, attached.text
        assert attached.json()["entity"]["id"] == entity_id

        # Same normalized name without a verified canonical domain is a possible duplicate,
        # not an automatic merge.
        duplicate = client.post(
            f"/v1/market/workspaces/{workspace_id}/entities",
            headers=headers(),
            json={
                "entity_kind": "Organization",
                "display_name": "  EXAMPLE East Africa Group ",
                "country_code": "KE",
            },
        )
        assert duplicate.status_code == 201, duplicate.text
        assert duplicate.json()["entity"]["id"] != entity_id
        assert entity_id in duplicate.json()["possible_duplicate_entity_ids"]

        listed = client.get(
            f"/v1/market/workspaces/{workspace_id}/entities", headers=headers()
        )
        assert listed.status_code == 200, listed.text
        assert len(listed.json()["items"]) == 2
        assert any(item["possible_duplicate_entity_ids"] for item in listed.json()["items"])

        # Candidate discovery never creates or mutates a Customer record.
        with org_session(UUID(ORG_A)) as session:
            assert session.scalar(
                select(func.count()).select_from(Customer).where(
                    Customer.organization_id == UUID(ORG_A)
                )
            ) == before_count
            original_customer = session.get(Customer, customer_a_id)
            assert original_customer is not None
            assert original_customer.name == "Existing CRM Customer"
            assert original_customer.tier == "B"
            assert original_customer.industry == "Existing industry"

        candidate_link = client.post(
            f"/v1/market/entities/{entity_id}/customer-links",
            headers=headers(),
            json={
                "customer_id": str(customer_a_id),
                "matching_basis": {
                    "signals": ["canonical_domain", "country_code"],
                    "confidence": "MEDIUM",
                    "review_required": True,
                },
            },
        )
        assert candidate_link.status_code == 201, candidate_link.text
        link_id = candidate_link.json()["id"]
        assert candidate_link.json()["link_status"] == "MATCH_CANDIDATE"
        assert candidate_link.json()["confirmed_by_user_id"] is None
        assert candidate_link.json()["confirmed_at"] is None

        cross_tenant_customer = client.post(
            f"/v1/market/entities/{entity_id}/customer-links",
            headers=headers(),
            json={"customer_id": str(customer_b_id), "matching_basis": {"signal": "name"}},
        )
        assert cross_tenant_customer.status_code == 404, cross_tenant_customer.text

        # Database composite FK must also reject (organization A, Customer B),
        # even if an application caller bypasses the HTTP-level tenant lookup.
        with org_session(UUID(ORG_A)) as session:
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(
                        MarketEntityCustomerLink(
                            organization_id=UUID(ORG_A),
                            entity_id=UUID(entity_id),
                            customer_id=customer_b_id,
                            link_status="MATCH_CANDIDATE",
                            matching_basis={"source": "negative-tenant-fixture"},
                        )
                    )
                    session.flush()

        confirmed = client.post(
            f"/v1/market/entities/{entity_id}/customer-links/{link_id}:confirm",
            headers=headers(),
        )
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["link_status"] == "USER_CONFIRMED"
        assert confirmed.json()["confirmed_by_user_id"] == ADMIN_A
        assert confirmed.json()["confirmed_at"] is not None

        with org_session(UUID(ORG_A)) as session:
            link = session.get(MarketEntityCustomerLink, UUID(link_id))
            assert link is not None
            assert link.link_status == "USER_CONFIRMED"
            assert link.confirmed_by_user_id == UUID(ADMIN_A)
            entity = session.get(MarketEntity, UUID(entity_id))
            assert entity is not None
            assert entity.status == "CANDIDATE"
            aliases = session.scalars(
                select(MarketEntityAlias).where(
                    MarketEntityAlias.organization_id == UUID(ORG_A),
                    MarketEntityAlias.entity_id == UUID(entity_id),
                )
            ).all()
            assert len(aliases) == 2
            assert session.scalar(
                select(func.count()).select_from(MarketActivityEvent).where(
                    MarketActivityEvent.organization_id == UUID(ORG_A),
                    MarketActivityEvent.workspace_id == UUID(workspace_id),
                    MarketActivityEvent.event_type == "CUSTOMER_MATCH_USER_CONFIRMED",
                )
            ) == 1

        # The Workspace entity and its candidate aliases are invisible under another tenant's GUC.
        with org_session(UUID(ORG_B)) as session:
            assert session.scalar(
                select(func.count()).select_from(MarketEntity).where(
                    MarketEntity.id == UUID(entity_id)
                )
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(MarketEntityAlias).where(
                    MarketEntityAlias.entity_id == UUID(entity_id)
                )
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(MarketWorkspaceEntity).where(
                    MarketWorkspaceEntity.entity_id == UUID(entity_id)
                )
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(MarketEntityCustomerLink).where(
                    MarketEntityCustomerLink.entity_id == UUID(entity_id)
                )
            ) == 0

    finally:
        with org_session(UUID(ORG_A)) as session:
            if workspace_id is not None:
                session.execute(
                    delete(MarketWorkspace).where(MarketWorkspace.id == UUID(workspace_id))
                )
            if other_workspace_id is not None:
                session.execute(
                    delete(MarketWorkspace).where(MarketWorkspace.id == UUID(other_workspace_id))
                )
            if entity_id is not None:
                session.execute(
                    delete(MarketEntity).where(MarketEntity.id == UUID(entity_id))
                )
            session.execute(delete(Customer).where(Customer.id == customer_a_id))
        with org_session(UUID(ORG_B)) as session:
            session.execute(delete(Customer).where(Customer.id == customer_b_id))
