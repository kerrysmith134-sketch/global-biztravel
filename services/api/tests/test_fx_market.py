"""FX 行情源同步（§7-8 / ADR-0033）：公开源写入同一 `fx_rates` 表，`source`/`as_of` 保留。

红线（与 ADR-0005 同源）：行情源未配置 ⇒ 任务显式 FAILED；行情缺失的币种进
`missing` 列表，绝不伪造汇率；折算与缺口逻辑（ADR-0019）完全不变——同步只是
替管理员往同一张表里写带来源的行。触发是 ADMIN 专属、幂等入队。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from app.config import get_settings
from app.db import org_session
from app.main import app
from app.models.fx import FxRate
from app.models.reliability import BackgroundJob
from app.services import fx_market_service, fx_service, job_service
from app.workers import tasks
from app.workers.tasks import TaskFailed
from fastapi.testclient import TestClient
from sqlalchemy import delete

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(org: str = ORG_A, user: str = USER_A) -> dict[str, str]:
    return {"X-Org-Id": org, "X-User-Id": user}


@dataclass(frozen=True)
class StubProvider:
    """确定性行情源桩：返回 ECB 风格的固定快照。"""

    name = "stub"
    rates: dict[str, str]
    quote_date: date = date(2026, 11, 6)

    def fetch_latest(self, base: str) -> fx_market_service.FxMarketQuote:
        return fx_market_service.FxMarketQuote(
            base=base,
            as_of=datetime(2026, 11, 6, tzinfo=UTC),
            rates={k: Decimal(v) for k, v in self.rates.items()},
        )


FRANKFURTER_SAMPLE = {
    "amount": 1.0,
    "base": "EUR",
    "date": "2026-11-06",
    "rates": {"USD": 1.0812, "CNY": 7.7841, "SGD": 1.4502},
}


def cleanup(job_ids: list[str]) -> None:
    with org_session(UUID(ORG_A)) as session:
        for job_id in job_ids:
            session.execute(delete(BackgroundJob).where(BackgroundJob.id == UUID(job_id)))
        session.execute(
            delete(FxRate).where(FxRate.organization_id == ORG_A, FxRate.source == "fx-stub")
        )


def run_fx_job(job_id: str) -> dict:
    with org_session(UUID(ORG_A)) as session:
        job = session.get(BackgroundJob, UUID(job_id))
        assert job is not None
        try:
            result = tasks.handle_fx_sync(session, job)
        except TaskFailed as exc:
            job_service.fail(session, job.id, error=exc.message, retryable=False)
            return {"status": "TASK_FAILED", "message": exc.message}
        # 与 Worker.main 一致：处理器只写数据，终态由这里结算。
        job_service.complete(session, job.id, result=result)
        return result


# ---------------------------------------------------------------------------
# Provider 解析（纯函数，无网络）
# ---------------------------------------------------------------------------


def test_parse_frankfurter_payload():
    quote = fx_market_service.parse_frankfurter(FRANKFURTER_SAMPLE)

    assert quote.base == "EUR"
    assert quote.as_of == datetime(2026, 11, 6, tzinfo=UTC)
    assert quote.rates["USD"] == Decimal("1.0812")
    assert quote.rates["CNY"] == Decimal("7.7841")


def test_parse_frankfurter_rejects_missing_date_or_rates():
    with pytest.raises(fx_market_service.FxMarketUnavailable):
        fx_market_service.parse_frankfurter({"base": "EUR", "rates": {}})
    with pytest.raises(fx_market_service.FxMarketUnavailable):
        fx_market_service.parse_frankfurter({"base": "EUR", "date": "2026-11-06"})


# ---------------------------------------------------------------------------
# 同步服务：写入同一张表，source/as_of 保留，缺口诚实
# ---------------------------------------------------------------------------


def test_sync_writes_rates_with_source_and_as_of():
    provider = StubProvider(rates={"USD": "1.0812", "CNY": "7.7841"})
    try:
        with org_session(UUID(ORG_A)) as session:
            summary = fx_market_service.sync_rates(
                session,
                ORG_A,
                base_currency="EUR",
                symbols=["USD", "CNY"],
                provider=provider,
            )
            assert summary["synced"] == 2
            assert summary["missing"] == []
            assert summary["source"] == "fx-stub"
            assert summary["as_of"] == "2026-11-06T00:00:00+00:00"

            # 按本测试的来源过滤：共享开发库里可能有真实行情行（更新 as_of），
            # latest_rates 只取每对最新一条，必须按 source 圈定断言作用域。
            rows = {
                row.quote_currency: row
                for row in session.query(FxRate).filter_by(
                    organization_id=ORG_A, source="fx-stub"
                )
            }
            assert set(rows) == {"USD", "CNY"}
            assert rows["USD"].rate == Decimal("1.0812")
            assert rows["USD"].source == "fx-stub"
            assert rows["USD"].as_of == datetime(2026, 11, 6, tzinfo=UTC)
            assert rows["USD"].created_by_user_id is None  # 行情行不归到任何管理员名下

            # 折算逻辑（ADR-0019）原样可用：直接记录生效（真实/桩来源都走同一条通路）
            quote = fx_service.conversion_for(session, ORG_A, base="EUR", currency="USD")
            assert quote is not None
            assert quote.via_inverse is False
    finally:
        cleanup([])


def test_sync_lists_missing_currencies_honestly():
    provider = StubProvider(rates={"USD": "1.0812"})  # ECB 没覆盖的币种不伪造
    try:
        with org_session(UUID(ORG_A)) as session:
            summary = fx_market_service.sync_rates(
                session,
                ORG_A,
                base_currency="EUR",
                symbols=["USD", "KRW"],
                provider=provider,
            )
            assert summary["synced"] == 1
            assert summary["missing"] == ["KRW"]

            assert fx_service.conversion_for(
                session, ORG_A, base="EUR", currency="KRW"
            ) is None
    finally:
        cleanup([])


def test_sync_idempotent_per_as_of_and_rejects_bad_symbols():
    from app.api.errors import Unprocessable

    provider = StubProvider(rates={"USD": "1.0812"})
    try:
        with org_session(UUID(ORG_A)) as session:
            first = fx_market_service.sync_rates(
                session, ORG_A, base_currency="EUR", symbols=["USD"], provider=provider
            )
            second = fx_market_service.sync_rates(
                session, ORG_A, base_currency="EUR", symbols=["USD"], provider=provider
            )
            assert first["synced"] == second["synced"] == 1

            with pytest.raises(Unprocessable):
                fx_market_service.sync_rates(
                    session, ORG_A, base_currency="EUR", symbols=["EUR"], provider=provider
                )
            with pytest.raises(Unprocessable):
                fx_market_service.sync_rates(
                    session, ORG_A, base_currency="EURO", symbols=["USD"], provider=provider
                )
    finally:
        cleanup([])


# ---------------------------------------------------------------------------
# 路由与任务：ADMIN 专属、幂等入队、未配置显式失败
# ---------------------------------------------------------------------------


def test_route_requires_admin(client: TestClient):
    # ORG_B 的 TRAVELER（种子数据）：同步是 ADMIN 专属动作，非管理员 403。
    traveler_headers = {
        "X-Org-Id": "33333333-3333-3333-3333-333333333333",
        "X-User-Id": "44444444-4444-4444-4444-444444444444",
    }
    response = client.post(
        "/v1/fx-rates:sync",
        headers={**traveler_headers, "Idempotency-Key": str(uuid.uuid4())},
        json={"base_currency": "EUR", "symbols": ["USD"]},
    )
    assert response.status_code == 403, response.text


def test_route_enqueues_job_idempotently(client: TestClient):
    key = str(uuid.uuid4())
    job_ids: list[str] = []
    try:
        first = client.post(
            "/v1/fx-rates:sync",
            headers={**headers(), "Idempotency-Key": key},
            json={"base_currency": "EUR", "symbols": ["USD", "CNY"]},
        )
        assert first.status_code == 202, first.text
        body = first.json()
        job_ids.append(body["job_id"])
        # 路由对 symbols 排序规范化（dedupe key 稳定性的前提）
        assert body["symbols"] == ["CNY", "USD"]
        assert body["reused"] is False

        # 同键同体 = 幂等回放（原始受理响应，含 reused=false）
        replay = client.post(
            "/v1/fx-rates:sync",
            headers={**headers(), "Idempotency-Key": key},
            json={"base_currency": "EUR", "symbols": ["USD", "CNY"]},
        )
        assert replay.status_code == 202, replay.text
        assert replay.json()["job_id"] == body["job_id"]

        # 换键同体 = 在途任务复用（dedupe），reused=true
        deduped = client.post(
            "/v1/fx-rates:sync",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={"base_currency": "EUR", "symbols": ["USD", "CNY"]},
        )
        assert deduped.status_code == 202, deduped.text
        assert deduped.json()["job_id"] == body["job_id"]
        assert deduped.json()["reused"] is True

        with org_session(UUID(ORG_A)) as session:
            job = session.get(BackgroundJob, UUID(body["job_id"]))
            assert job is not None
            assert job.job_type == "fx_rate_sync"
            assert job.payload["base_currency"] == "EUR"
            assert job.payload["symbols"] == ["CNY", "USD"]
    finally:
        cleanup(job_ids)


def test_handler_marks_job_failed_when_provider_unconfigured(
    client: TestClient, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "fx_provider", "unavailable", raising=False)
    job_ids: list[str] = []
    try:
        accepted = client.post(
            "/v1/fx-rates:sync",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={"base_currency": "EUR", "symbols": ["USD"]},
        )
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["job_id"]
        job_ids.append(job_id)

        result = run_fx_job(job_id)
        assert result["status"] == "TASK_FAILED"

        with org_session(UUID(ORG_A)) as session:
            job = session.get(BackgroundJob, UUID(job_id))
            assert job is not None
            assert job.status == "FAILED"
            assert "未配置" in (job.last_error or "")
    finally:
        cleanup(job_ids)


def test_handler_syncs_rates_end_to_end(client: TestClient, monkeypatch):
    # 注入桩 provider：与不可用路径同一条处理器链路。
    from app.services import fx_market_service as svc

    monkeypatch.setattr(svc, "get_fx_provider", lambda: StubProvider(rates={"USD": "1.0812"}))
    job_ids: list[str] = []
    try:
        accepted = client.post(
            "/v1/fx-rates:sync",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={"base_currency": "EUR", "symbols": ["USD"]},
        )
        assert accepted.status_code == 202, accepted.text
        job_id = accepted.json()["job_id"]
        job_ids.append(job_id)

        result = run_fx_job(job_id)
        assert result["synced"] == 1

        with org_session(UUID(ORG_A)) as session:
            job = session.get(BackgroundJob, UUID(job_id))
            assert job is not None
            assert job.status == "SUCCEEDED"
            assert job.result["synced"] == 1
    finally:
        cleanup(job_ids)
