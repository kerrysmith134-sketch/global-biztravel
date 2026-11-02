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
from app.models.crm import CrmSyncJob
from app.models.trip import Trip, Visit
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
