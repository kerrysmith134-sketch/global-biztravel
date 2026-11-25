"""纪要分类洞察（PRD P1：自动识别拜访纪要、客户需求和后续任务）验收。

对应 PRD 页面 07 §8.2：AI 提取后必须区分摘要/客户需求/商机信号/交易条件/商业承诺/
阶段变化，且**不得把推断直接写成已确认事实**——非摘要类一律 requires_confirmation=True，
由服务端强制（模型说 false 也改回来）。本测试同时验证"不写 CRM"：全程无 CRM 副作用。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.assistant import AssistantConversation
from app.models.crm import CrmSyncJob
from app.models.trip import Trip, Visit
from app.models.workflow import AgentRun
from app.services import minutes_service
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ADMIN_A = "55555555-5555-5555-5555-555555555555"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers() -> dict[str, str]:
    return {
        "X-Org-Id": ORG_A,
        "X-User-Id": ADMIN_A,
        "Idempotency-Key": str(uuid.uuid4()),
    }


def make_visit_with_notes(client: TestClient, notes: str) -> tuple[str, str]:
    now = datetime.now(UTC)
    name = uuid.uuid4().hex[:8]
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"洞察验收·{name}",
            "window_start": (now + timedelta(days=3)).isoformat(),
            "window_end": (now + timedelta(days=5)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    place = client.post(
        "/v1/places",
        headers=headers(),
        json={"name": f"洞察地点·{name}", "city": "Berlin", "country_code": "DE", "lat": 52.52, "lon": 13.405},
    )
    assert place.status_code == 201, place.text
    visit = client.post(
        f"/v1/trips/{trip.json()['id']}/visits",
        headers=headers(),
        json={"place_id": place.json()["id"], "title": "洞察拜访", "service_minutes": 60},
    )
    assert visit.status_code == 201, visit.text
    visit_id = visit.json()["id"]
    record = client.post(
        f"/v1/visits/{visit_id}/records",
        headers=headers(),
        json={
            "client_record_id": str(uuid.uuid4()),
            "outcome": "MET",
            "notes": notes,
            "client_updated_at": now.isoformat(),
            "photos": [],
            "follow_up_tasks": [],
        },
    )
    assert record.status_code == 201, record.text
    return trip.json()["id"], visit_id


def test_insights_classified_and_confirmation_forced(client: TestClient) -> None:
    notes = (
        "客户关注产品稳定性；希望支持 800V 快充接口；"
        "计划明年扩产，报价按 500 台给 9 折；承诺 12 月前完成交付。"
    )
    trip_id, visit_id = make_visit_with_notes(client, notes)
    try:
        resp = client.post(
            f"/v1/visits/{visit_id}/minutes:draft", headers=headers(), json={}
        )
        assert resp.status_code == 200, resp.text
        insights = resp.json()["insights"]
        kinds = {item["kind"] for item in insights}
        # 关键词命中：摘要 + 客户需求 + 商机信号 + 交易条件 + 商业承诺。
        assert {"SUMMARY", "CUSTOMER_NEED", "OPPORTUNITY_SIGNAL", "DEAL_TERM", "COMMITMENT"} <= kinds, kinds

        for item in insights:
            if item["kind"] == "SUMMARY":
                assert item["requires_confirmation"] is False, item
            else:
                # PRD 红线：非摘要类必须人工确认，不能当已确认事实。
                assert item["requires_confirmation"] is True, item
            assert item["text"].strip(), item

        # 不写 CRM：纪要只是草稿，不产生任何 CRM 同步任务。
        with org_session(UUID(ORG_A)) as session:
            crm_count = session.scalar(
                select(func.count()).select_from(CrmSyncJob).where(
                    CrmSyncJob.organization_id == UUID(ORG_A)
                )
            )
            assert int(crm_count or 0) == 0
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Visit).where(Visit.id == UUID(visit_id)))
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))



def test_assistant_meeting_minutes_tool_requires_tap_and_keeps_followups_unconfirmed(
    client: TestClient,
) -> None:
    raw_notes = (
        "CONFIDENTIAL RAW CUSTOMER NOTE: customer needs 800V fast charging; "
        "send the technical proposal before Friday."
    )
    trip_id, visit_id = make_visit_with_notes(client, raw_notes)
    conversation_id: str | None = None

    try:
        conversation = client.post(
            "/v1/assistant/conversations",
            headers=headers(),
            json={"trip_id": trip_id, "title": "会议纪要草稿"},
        )
        assert conversation.status_code == 201, conversation.text
        conversation_id = conversation.json()["id"]

        proposed = client.post(
            f"/v1/assistant/conversations/{conversation_id}/messages",
            headers=headers(),
            json={"content": "请为这次拜访生成会议纪要草稿"},
        )
        assert proposed.status_code == 200, proposed.text
        tool_cards = [
            card
            for card in proposed.json()["cards"]
            if card.get("tool_name") == "visit.draft_minutes"
        ]
        assert len(tool_cards) == 1
        proposal = tool_cards[0]
        assert proposal["entity_id"] == str(visit_id)
        assert proposal["tool_arguments"] == {
            "visit_id": str(visit_id),
            "tone": "PROFESSIONAL",
        }

        # Creating a proposal card is read-only: no AgentRun and no generated minutes yet.
        with org_session(UUID(ORG_A)) as session:
            before = list(
                session.scalars(
                    select(AgentRun).where(
                        AgentRun.organization_id == UUID(ORG_A),
                        AgentRun.agent_type == "tool:visit.draft_minutes",
                        AgentRun.input["arguments"]["visit_id"].astext == visit_id,
                    )
                )
            )
            assert before == []

        endpoint = f"/v1/assistant/conversations/{conversation_id}/tools:execute"
        request_headers = headers()
        key = str(uuid.uuid4())
        request_headers["Idempotency-Key"] = key
        payload = {
            "tool_name": "visit.draft_minutes",
            "arguments": {
                "visit_id": visit_id,
                "tone": "PROFESSIONAL",
            },
        }
        first = client.post(endpoint, headers=request_headers, json=payload)
        assert first.status_code == 200, first.text
        result = first.json()
        assert result["role"] == "ASSISTANT"
        assert result["cards"][0]["type"] == "TOOL_RESULT"
        assert result["cards"][0]["title"] in {"会议纪要草稿", "缺少现场记录"}
        assert "会议纪要仅为草稿" in result["content"]
        assert "人工确认" in result["content"]

        replay = client.post(endpoint, headers=request_headers, json=payload)
        assert replay.status_code == 200, replay.text
        assert replay.headers.get("Idempotent-Replay") == "true"
        assert replay.json() == result

        conversation_detail = client.get(
            f"/v1/assistant/conversations/{conversation_id}",
            headers=headers(),
        )
        assert conversation_detail.status_code == 200, conversation_detail.text
        minutes_messages = [
            message
            for message in conversation_detail.json()["messages"]
            if any(
                card.get("type") == "TOOL_RESULT"
                and card.get("title") in {"会议纪要草稿", "缺少现场记录"}
                for card in message.get("cards", [])
            )
        ]
        assert len(minutes_messages) == 1

        with org_session(UUID(ORG_A)) as session:
            runs = list(
                session.scalars(
                    select(AgentRun).where(
                        AgentRun.organization_id == UUID(ORG_A),
                        AgentRun.agent_type == "tool:visit.draft_minutes",
                        AgentRun.input["arguments"]["visit_id"].astext == visit_id,
                    )
                )
            )
            assert len(runs) == 1
            run = runs[0]
            assert run.status == "SUCCEEDED"
            assert run.input["arguments"] == {
                "visit_id": str(visit_id),
                "tone": "PROFESSIONAL",
            }
            assert "notes" not in run.input
            assert raw_notes not in str(run.input)
            assert run.output["visit_id"] == str(visit_id)
            assert run.output["llm_invocation_id"]
            assert "follow_up_tasks" in run.output

            # Structured follow-ups are discoverable as suggestions only; no formal task exists.
            from app.services import visit_follow_up_service

            feed = visit_follow_up_service.list_follow_ups(
                session, UUID(ORG_A), UUID(visit_id)
            )
            assert feed["items"] == []
            for suggestion in feed["suggestions"]:
                assert suggestion["source_type"] == "MINUTES_DRAFT"
                assert suggestion["source_agent_run_id"] == run.id

            crm_count = session.scalar(
                select(func.count())
                .select_from(CrmSyncJob)
                .where(CrmSyncJob.organization_id == UUID(ORG_A))
            )
            assert int(crm_count or 0) == 0
    finally:
        with org_session(UUID(ORG_A)) as session:
            if conversation_id is not None:
                session.execute(
                    delete(AssistantConversation).where(
                        AssistantConversation.id == UUID(conversation_id)
                    )
                )
            session.execute(
                delete(AgentRun).where(
                    AgentRun.organization_id == UUID(ORG_A),
                    AgentRun.agent_type == "tool:visit.draft_minutes",
                    AgentRun.input["arguments"]["visit_id"].astext == visit_id,
                )
            )
            session.execute(delete(Visit).where(Visit.id == UUID(visit_id)))
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))



def test_normalise_insights_enforces_business_rule() -> None:
    """模型输出不可信：非法类别丢弃，非摘要类别强制确认。"""
    out = minutes_service._normalise_insights(
        [
            {"kind": "SUMMARY", "text": "客户关注稳定性", "requires_confirmation": True},
            {"kind": "DEAL_TERM", "text": "500 台 9 折", "requires_confirmation": False},
            {"kind": "MAGIC_KIND", "text": "不该出现", "requires_confirmation": False},
            {"kind": "CUSTOMER_NEED", "text": "", "requires_confirmation": True},
            "not-a-dict",
        ]
    )
    assert [(i.kind, i.requires_confirmation) for i in out] == [
        ("SUMMARY", False),
        ("DEAL_TERM", True),
    ]


def test_template_fallback_has_no_fake_insights(client: TestClient) -> None:
    """LLM 不可用时的模板兜底只回摘要类，不猜客户需求/交易条件。"""
    from app.llm.contracts import LLMUnavailable  # noqa: F401  (文档用途)

    fallback = minutes_service._fallback("拜访", "客户", "", [])
    assert fallback["insights"] == []
    with_notes = minutes_service._fallback("拜访", "客户", "客户要求 12 月前交付", [])
    assert len(with_notes["insights"]) == 1
    assert with_notes["insights"][0]["kind"] == "SUMMARY"
    assert with_notes["insights"][0]["requires_confirmation"] is False
