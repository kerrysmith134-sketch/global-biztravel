"""M3 验收：LLM 网关、调用审计、会议纪要草稿与降级路径。

覆盖设计文档第 7 章：

- 每条草稿都有一条 ``llm_invocations`` 记录（模型 / prompt 版本 / token / 延迟）。
- 相同输入命中重放缓存：第二次仍留调用记录，但 ``cached=true`` 且不再计 token。
- 工具目录列出 ``visit.draft_minutes`` 且标注需要人审。
- provider 不可用 ⇒ 模板兜底、``degraded=true``、``error`` 有原因，业务不中断。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.llm.contracts import LLMRequest, ProviderResult
from app.llm.gateway import LLMGateway
from app.main import app
from app.models.geo import Customer, Place
from app.models.llm import LLMInvocation
from app.models.trip import Trip
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


class _FailingProvider:
    name = "failing"

    def complete(self, request: LLMRequest) -> ProviderResult:  # noqa: ARG002
        raise RuntimeError("provider offline")


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers() -> dict[str, str]:
    return {"X-Org-Id": ORG_A, "X-User-Id": USER_A}


def create_visit_with_record(client: TestClient) -> tuple[str, str, str, str]:
    now = datetime.now(UTC)
    name = uuid.uuid4().hex[:8]
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"M3 纪要验收·{name}",
            "window_start": (now + timedelta(days=3)).isoformat(),
            "window_end": (now + timedelta(days=5)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]

    customer = client.post(
        "/v1/customers",
        headers=headers(),
        json={"name": f"M3 客户·{name}", "country_code": "DE", "tier": "A"},
    )
    assert customer.status_code == 201, customer.text
    customer_id = customer.json()["id"]

    place = client.post(
        "/v1/places",
        headers=headers(),
        json={
            "name": f"M3 地点·{name}",
            "city": "Berlin",
            "country_code": "DE",
            "lat": 52.52,
            "lon": 13.405,
        },
    )
    assert place.status_code == 201, place.text
    place_id = place.json()["id"]

    visit = client.post(
        f"/v1/trips/{trip_id}/visits",
        headers=headers(),
        json={
            "place_id": place_id,
            "customer_id": customer_id,
            "title": "续约沟通会",
            "service_minutes": 60,
        },
    )
    assert visit.status_code == 201, visit.text
    visit_id = visit.json()["id"]

    record = client.post(
        f"/v1/visits/{visit_id}/records",
        headers=headers(),
        json={
            "client_record_id": str(uuid.uuid4()),
            "outcome": "MET",
            "notes": "客户确认续约意向，要求 11 月中旬前给出报价",
            "client_updated_at": now.isoformat(),
            "photos": [],
            "follow_up_tasks": [{"title": "11 月中旬前提交续约报价", "due_date": "2026-11-15"}],
        },
    )
    assert record.status_code == 201, record.text
    return trip_id, customer_id, place_id, visit_id


def cleanup(trip_id: str, customer_id: str, place_id: str, since: datetime) -> None:
    with org_session(UUID(ORG_A)) as session:
        session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
        session.execute(delete(Place).where(Place.id == UUID(place_id)))
        # 清掉本用例产生的调用记录（审计表是共享开发库，测试自己要收尾）。
        session.execute(
            delete(LLMInvocation).where(
                LLMInvocation.organization_id == UUID(ORG_A),
                LLMInvocation.created_at >= since,
                LLMInvocation.task.in_(("draft_meeting_minutes", "test_degraded")),
            )
        )


def test_minutes_draft_records_invocation_and_replays_from_cache(client: TestClient) -> None:
    since = datetime.now(UTC) - timedelta(seconds=1)
    trip_id, customer_id, place_id, visit_id = create_visit_with_record(client)
    try:
        first = client.post(
            f"/v1/visits/{visit_id}/minutes:draft",
            headers={**headers(), "Idempotency-Key": f"minutes-{uuid.uuid4().hex}"},
            json={"tone": "PROFESSIONAL"},
        )
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["degraded"] is False
        assert body["summary"]
        assert any("报价" in task["title"] for task in body["follow_up_tasks"])
        assert body["draft_id"] == body["llm_invocation_id"]

        with org_session(UUID(ORG_A)) as session:
            invocations = list(
                session.scalars(
                    select(LLMInvocation).where(
                        LLMInvocation.organization_id == UUID(ORG_A),
                        LLMInvocation.created_at >= since,
                        LLMInvocation.task == "draft_meeting_minutes",
                    )
                )
            )
        assert len(invocations) == 1
        invocation = invocations[0]
        assert invocation.model == "stub"
        assert invocation.prompt_template_id == "meeting_minutes"
        assert invocation.prompt_version == "v1"
        assert invocation.degraded is False
        assert invocation.cached is False
        assert invocation.tokens_in > 0 and invocation.tokens_out > 0
        assert invocation.trace_id

        # 同样输入（换一个幂等键，确保走业务）→ 网关缓存命中，仍写一条 cached 记录。
        second = client.post(
            f"/v1/visits/{visit_id}/minutes:draft",
            headers={**headers(), "Idempotency-Key": f"minutes-{uuid.uuid4().hex}"},
            json={"tone": "PROFESSIONAL"},
        )
        assert second.status_code == 200, second.text
        assert second.json()["draft_id"] != body["draft_id"]
        assert second.json()["summary"] == body["summary"]

        with org_session(UUID(ORG_A)) as session:
            rows = list(
                session.scalars(
                    select(LLMInvocation)
                    .where(
                        LLMInvocation.organization_id == UUID(ORG_A),
                        LLMInvocation.created_at >= since,
                        LLMInvocation.task == "draft_meeting_minutes",
                    )
                    .order_by(LLMInvocation.created_at)
                )
            )
        assert len(rows) == 2
        assert rows[0].cached is False
        assert rows[1].cached is True
        assert rows[1].tokens_in == 0 and rows[1].tokens_out == 0

        # 审计接口（仅 ADMIN/OWNER）能查到调用记录。
        audit = client.get(
            "/v1/llm/invocations", headers=headers(), params={"task": "draft_meeting_minutes"}
        )
        assert audit.status_code == 200, audit.text
        assert any(item["cached"] is True for item in audit.json())
    finally:
        cleanup(trip_id, customer_id, place_id, since)
        # 客户由本用例创建；出行/地点由 cleanup 删除，客户不随节点级联回收。
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Customer).where(Customer.id == UUID(customer_id)))


def test_tool_catalog_marks_approval() -> None:
    client = TestClient(app)
    resp = client.get("/v1/llm/tools", headers=headers())
    assert resp.status_code == 200, resp.text
    tools = {item["name"]: item for item in resp.json()}
    assert "visit.draft_minutes" in tools
    assert tools["visit.draft_minutes"]["requires_approval"] is True
    assert tools["visit.draft_minutes"]["idempotent"] is True


def test_gateway_degrades_to_fallback_when_provider_fails() -> None:
    gateway = LLMGateway(provider=_FailingProvider())
    request = LLMRequest(
        task="test_degraded",
        prompt_template_id="test",
        prompt_version="v1",
        variables={"value": "x"},
        schema={"type": "object", "required": ["title"]},
        fallback={"title": "模板兜底", "summary": "provider 不可用"},
    )
    since = datetime.now(UTC) - timedelta(seconds=1)
    try:
        with org_session(UUID(ORG_A)) as session:
            response = gateway.complete(session, UUID(ORG_A), request)
            assert response.degraded is True
            assert response.error and "offline" in response.error
            assert response.output["title"] == "模板兜底"

            row = session.scalars(
                select(LLMInvocation).where(LLMInvocation.id == response.invocation_id)
            ).one()
            assert row.degraded is True
            assert row.error and "offline" in row.error
            assert row.cached is False
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(LLMInvocation).where(
                    LLMInvocation.organization_id == UUID(ORG_A),
                    LLMInvocation.created_at >= since,
                    LLMInvocation.task == "test_degraded",
                )
            )
