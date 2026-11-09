"""自然语言创建出差（PRD P0-2：对话与 Excel 导入）验收。

链路：消息 → trip_intake 结构化解析（不编造缺失字段）→ TRIP_PROPOSAL 提案卡
→ 人确认（白名单动作 create_trip_draft）→ 服务端二次校验 → 草稿行程（DRAFT，
不对外预订）。同时验证：信息不足时反问而不是猜、重复创建幂等拒绝。
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.assistant import AssistantConversation, AssistantMessage
from app.models.trip import Trip
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


def headers(org: str = ORG_A, user: str = USER_A) -> dict[str, str]:
    return {
        "X-Org-Id": org,
        "X-User-Id": user,
        "Idempotency-Key": str(uuid.uuid4()),
    }


def new_conversation(client: TestClient) -> str:
    resp = client.post("/v1/assistant/conversations", headers=headers(), json={})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def send(client: TestClient, conversation_id: str, message: str) -> dict:
    resp = client.post(
        f"/v1/assistant/conversations/{conversation_id}/messages",
        headers=headers(),
        json={"content": message},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_full_request_creates_draft_after_confirmation(client: TestClient) -> None:
    conversation_id = new_conversation(client)
    created_trip_id: str | None = None
    try:
        # 明确的创建请求：国家 + 天数 + 起点日期。
        start = date.today() + timedelta(days=21)
        bot = send(
            client,
            conversation_id,
            f"帮我创建一次出差：德国和新加坡，{start.isoformat()} 出发，共 10 天。",
        )
        proposal = next(
            (card for card in bot["cards"] if card["type"] == "TRIP_PROPOSAL"), None
        )
        assert proposal is not None, bot
        payload = proposal["payload"]
        assert payload["countries"] == ["DE", "SG"]
        assert payload["start_date"] == start.isoformat()
        # 10 天含出发日 → 结束 = start + 9
        assert payload["end_date"] == (start + timedelta(days=9)).isoformat()
        assert "创建" in bot["content"] or "草稿" in bot["content"]

        # 人确认 → 白名单动作创建草稿行程。
        action = client.post(
            f"/v1/assistant/conversations/{conversation_id}/actions",
            headers=headers(),
            json={"action": "create_trip_draft", "subject_id": proposal["entity_id"]},
        )
        assert action.status_code == 200, action.text
        result_card = action.json()["cards"][0]
        assert result_card["type"] == "ACTION_RESULT"
        created_trip_id = result_card["entity_id"]

        trip_resp = client.get(f"/v1/trips/{created_trip_id}", headers=headers())
        assert trip_resp.status_code == 200, trip_resp.text
        trip = trip_resp.json()
        assert trip["status"] == "DRAFT"
        assert trip["countries"] == ["DE", "SG"]
        from zoneinfo import ZoneInfo
        window_start = datetime.fromisoformat(trip["window_start"].replace("Z", "+00:00"))
        local_day = window_start.astimezone(ZoneInfo("Asia/Shanghai")).date()
        assert local_day == start

        # 同一提案重复确认 → 幂等拒绝，不产生第二条行程。
        repeat = client.post(
            f"/v1/assistant/conversations/{conversation_id}/actions",
            headers=headers(),
            json={"action": "create_trip_draft", "subject_id": proposal["entity_id"]},
        )
        assert repeat.status_code in (400, 422)
    finally:
        with org_session(UUID(ORG_A)) as session:
            if created_trip_id:
                session.execute(delete(Trip).where(Trip.id == UUID(created_trip_id)))
            session.execute(
                delete(AssistantConversation).where(
                    AssistantConversation.id == UUID(conversation_id)
                )
            )


def test_incomplete_request_asks_instead_of_guessing(client: TestClient) -> None:
    conversation_id = new_conversation(client)
    try:
        # 有目的地、无天数/日期：不编造窗口，反问缺失信息，不产生提案卡。
        bot = send(client, conversation_id, "帮我创建一次去芬兰的出差")
        assert not [c for c in bot["cards"] if c["type"] == "TRIP_PROPOSAL"]
        assert "缺" in bot["content"] or "补充" in bot["content"], bot["content"]
        with org_session(UUID(ORG_A)) as session:
            proposals = session.scalars(
                select(AssistantMessage).where(
                    AssistantMessage.conversation_id == UUID(conversation_id),
                    AssistantMessage.role == "ASSISTANT",
                )
            ).all()
            assert all(
                not any(c.get("type") == "TRIP_PROPOSAL" for c in (m.cards or []))
                for m in proposals
            )
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(AssistantConversation).where(
                    AssistantConversation.id == UUID(conversation_id)
                )
            )


def test_relative_dates_parsed_and_proposed(client: TestClient) -> None:
    conversation_id = new_conversation(client)
    try:
        bot = send(client, conversation_id, "两周后出发去新加坡出差 7 天")
        proposal = next(
            (card for card in bot["cards"] if card["type"] == "TRIP_PROPOSAL"), None
        )
        assert proposal is not None, bot
        payload = proposal["payload"]
        assert payload["countries"] == ["SG"]
        assert payload["start_date"] == (date.today() + timedelta(weeks=2)).isoformat()
        # 7 天含出发日 → 结束 = start + 6
        assert payload["end_date"] == (date.today() + timedelta(weeks=2, days=6)).isoformat()
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(AssistantConversation).where(
                    AssistantConversation.id == UUID(conversation_id)
                )
            )


def test_status_question_is_not_a_creation_request(client: TestClient) -> None:
    conversation_id = new_conversation(client)
    try:
        # 只问状态：不能被误判为创建请求。
        bot = send(client, conversation_id, "这趟行程现在需要我做什么？")
        assert not [c for c in bot["cards"] if c["type"] == "TRIP_PROPOSAL"]
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(AssistantConversation).where(
                    AssistantConversation.id == UUID(conversation_id)
                )
            )


def test_action_requires_proposal_and_is_tenant_scoped(client: TestClient) -> None:
    conversation_id = new_conversation(client)
    try:
        # subject 不是提案消息 → 拒绝。
        bot = send(client, conversation_id, "这趟行程现在需要我做什么？")
        bad = client.post(
            f"/v1/assistant/conversations/{conversation_id}/actions",
            headers=headers(),
            json={"action": "create_trip_draft", "subject_id": bot["id"]},
        )
        assert bad.status_code in (400, 422)

        # 跨租户不能对别人的对话执行动作。
        stolen = client.post(
            f"/v1/assistant/conversations/{conversation_id}/actions",
            headers=headers(ORG_B, USER_B),
            json={"action": "create_trip_draft", "subject_id": bot["id"]},
        )
        assert stolen.status_code == 404
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(AssistantConversation).where(
                    AssistantConversation.id == UUID(conversation_id)
                )
            )


# ---------------------------------------------------------------------------
# 回归：相对日期必须按「请求当天」解析，不能被 LLM 重放缓存冻结。
# 背景：v1 的 input_hash 只含 message，跨天后同一句"两周后出发"会命中昨天的
# 缓存并返回旧日期（P0-2 真实缺陷：11-07 的缓存让 11-09 的请求解析出 11-21）。
def test_relative_dates_follow_variables_today() -> None:
    from app.llm.contracts import LLMRequest
    from app.llm.providers import StubProvider

    provider = StubProvider()
    message = "两周后出发去新加坡出差 7 天"

    def parse(today: date) -> dict:
        request = LLMRequest(
            task="trip_intake",
            prompt_template_id="trip_intake",
            prompt_version="v2",
            variables={"message": message, "today": today.isoformat()},
        )
        return provider.complete(request).output

    day1 = date.today()
    day2 = day1 + timedelta(days=1)
    assert parse(day1)["start_date"] == (day1 + timedelta(weeks=2)).isoformat()
    assert parse(day2)["start_date"] == (day2 + timedelta(weeks=2)).isoformat()


def test_trip_intake_cache_key_includes_today() -> None:
    """同一消息的不同基准日必须产生不同 input_hash，否则会命中过期缓存。"""
    from app.llm.contracts import LLMRequest
    from app.llm.gateway import LLMGateway

    def digest(today: str) -> str:
        request = LLMRequest(
            task="trip_intake",
            prompt_template_id="trip_intake",
            prompt_version="v2",
            variables={"message": "两周后出发去新加坡出差 7 天", "today": today},
        )
        return LLMGateway._input_hash(request, "stub")

    assert digest("2026-11-07") != digest("2026-11-09")
    # 同一天内可重放（缓存语义仍然有效）。
    assert digest("2026-11-09") == digest("2026-11-09")
