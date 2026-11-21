"""Market Workspace / Context Snapshot / Research Question 的 PostgreSQL 集成验收。"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.market import (
    MarketActivityEvent,
    MarketContextSnapshot,
    MarketResearchQuestion,
    MarketWorkspace,
)
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

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
    finally:
        if workspace_id is not None:
            with org_session(UUID(ORG_A)) as session:
                session.execute(
                    delete(MarketWorkspace).where(MarketWorkspace.id == UUID(workspace_id))
                )
