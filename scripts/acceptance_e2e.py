#!/usr/bin/env python
"""真实后端端到端验收（AT-001 ~ AT-012 的服务端链路）。

它不模拟任何 API：脚本用真实 HTTP 打运行中的 FastAPI，用真实 PostgreSQL 落库，并通过
``app.workers.main --once`` 驱动后台求解。覆盖：

创建多国 Trip → 建出发地 → Excel 导入客户 → 校验/落库 → 生成候选计划 → 独立验证 →
激活 → 现场记录 → 离线同步 → 会议纪要草稿 → 完成出行 → 复盘记忆 → 指标 → 审计时间线。

前置：``make db-up`` 与 ``make api`` 已在运行。用法：

    .venv/bin/python scripts/acceptance_e2e.py
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "docs" / "acceptance-tests" / "fixtures" / "sample-customers.xlsx"
BASE = os.environ.get("GBT_E2E_BASE", "http://127.0.0.1:8080")
ORG = os.environ.get("GBT_DEV_ORG_ID", "11111111-1111-1111-1111-111111111111")
USER = os.environ.get("GBT_DEV_USER_ID", "22222222-2222-2222-2222-222222222222")

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: 本次脚本创建的对象；无论成功失败都在 `finally` 里清理。
_CREATED: dict[str, str | None] = {"trip": None, "place": None}


class CheckFailed(RuntimeError):
    pass


def check(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)
    print(f"  ✓ {message}")


def headers() -> dict[str, str]:
    return {
        "X-Org-Id": ORG,
        "X-User-Id": USER,
        "Idempotency-Key": str(uuid.uuid4()),
    }


def run_worker_once() -> None:
    env = os.environ.copy()
    subprocess.run(
        [sys.executable, "-m", "app.workers.main", "--once", "--org", ORG],
        cwd=ROOT / "services" / "api",
        env=env,
        check=True,
    )


def poll_job(client: httpx.Client, job_id: str, *, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    last: dict = {}
    while time.monotonic() < deadline:
        resp = client.get(f"/v1/jobs/{job_id}", headers=headers())
        resp.raise_for_status()
        last = resp.json()
        if last["status"] in ("SUCCEEDED", "FAILED", "DEAD"):
            return last
        time.sleep(0.3)
    return last


def cleanup(trip_id: str, origin_place_id: str) -> None:
    """删掉本次脚本创建的出行与出发地；导入批次随 Trip 级联。"""
    sys.path.insert(0, str(ROOT / "services" / "api"))
    from app.db import org_session
    from app.models.fx import FxRate
    from app.models.geo import Place
    from app.models.reliability import OutboxEvent
    from app.models.trip import Trip
    from sqlalchemy import delete

    with org_session(UUID(ORG)) as session:
        session.execute(delete(OutboxEvent).where(OutboxEvent.aggregate_id == UUID(trip_id)))
        session.execute(delete(FxRate).where(FxRate.source == f"e2e:{trip_id}"))
        session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
        session.execute(delete(Place).where(Place.id == UUID(origin_place_id)))


def main() -> int:
    name = f"AT-E2E 全流程·{uuid.uuid4().hex[:8]}"
    now = datetime.now(UTC)
    trip_id: str | None = None
    origin_place_id: str | None = None

    with httpx.Client(base_url=BASE, timeout=30) as client:
        print("1) 健康检查")
        health = client.get("/v1/health")
        health.raise_for_status()
        check(health.json()["database"] == "up", "数据库可用")

        print("2) 创建出发地与多国出行")
        origin = client.post(
            "/v1/places",
            headers=headers(),
            json={
                "name": f"E2E 上海出发地·{uuid.uuid4().hex[:6]}",
                "city": "Shanghai",
                "country_code": "CN",
                "lat": 31.2304,
                "lon": 121.4737,
                "timezone": "Asia/Shanghai",
            },
        )
        origin.raise_for_status()
        origin_place_id = origin.json()["id"]
        _CREATED["place"] = origin_place_id

        trip = client.post(
            "/v1/trips",
            headers=headers(),
            json={
                "name": name,
                "purpose": "端到端验收",
                "origin_place_id": origin_place_id,
                "window_start": (now + timedelta(days=10)).isoformat(),
                "window_end": (now + timedelta(days=15)).isoformat(),
                "countries": ["DE", "NL"],
                "currency": "EUR",
            },
        )
        trip.raise_for_status()
        trip_id = trip.json()["id"]
        _CREATED["trip"] = trip_id
        check(trip.status_code == 201, f"创建出行 {trip_id}")

        print("3) Excel 两阶段导入")
        if not FIXTURE.exists():
            raise CheckFailed(f"缺少导入夹具 {FIXTURE}")
        parsed = client.post(
            f"/v1/trips/{trip_id}/imports:parse",
            headers=headers(),
            files={"file": (FIXTURE.name, FIXTURE.read_bytes(), XLSX_MIME)},
        )
        parsed.raise_for_status()
        parse_body = parsed.json()
        check(parse_body["total_rows"] > 0, f"解析 {parse_body['total_rows']} 行")
        check(parse_body["valid_rows"] > 0, f"可导入 {parse_body['valid_rows']} 行")

        confirmed = client.post(
            f"/v1/trips/{trip_id}/imports:confirm",
            headers=headers(),
            json={"import_id": parse_body["import_id"], "accepted_row_numbers": []},
        )
        confirmed.raise_for_status()
        confirm_body = confirmed.json()
        check(confirm_body["created_visits"] > 0, f"落库 {confirm_body['created_visits']} 个拜访")

        visits = client.get(f"/v1/trips/{trip_id}/visits", headers=headers())
        visits.raise_for_status()
        visit_list = visits.json()
        check(len(visit_list) > 0, "拜访列表可见")
        visit_id = visit_list[0]["id"]

        # 跨洲行程必须按航空口径规划；导入默认 GROUND 会把上海→欧洲段算成数天。
        for item in visit_list:
            patched = client.patch(
                f"/v1/visits/{item['id']}", headers=headers(), json={"movement_mode": "AIR"}
            )
            patched.raise_for_status()
        check(True, "拜访交通方式设为 AIR")

        print("4) 生成候选计划（后台 Worker 真实求解）")
        generated = client.post(f"/v1/trips/{trip_id}/plans:generate", headers=headers())
        generated.raise_for_status()
        job_id = generated.json()["job_id"]
        run_worker_once()
        job = poll_job(client, job_id)
        if job["status"] == "PENDING":
            # 单轮 Worker 可能在其启动瞬间还没看到刚提交的任务；真实 Worker 是循环的，
            # 这里再跑一轮等价于"下一轮轮询"。
            time.sleep(0.5)
            run_worker_once()
            job = poll_job(client, job_id)
        check(job["status"] == "SUCCEEDED", f"求解任务完成（{job['status']}）")

        plans = client.get(f"/v1/trips/{trip_id}/plans", headers=headers())
        plans.raise_for_status()
        versions = plans.json()
        check(len(versions) > 0, "生成计划版本")
        plan_id = max(versions, key=lambda item: item["version_number"])["id"]
        detail = client.get(f"/v1/plan-versions/{plan_id}", headers=headers())
        detail.raise_for_status()
        detail_body = detail.json()
        check(detail_body["validation"]["feasible"] is True, "独立验证器判定可行")
        check(len(detail_body.get("stops", [])) > 0, "计划包含停靠")

        print("5) 激活计划（If-Match 乐观并发）")
        current = client.get(f"/v1/trips/{trip_id}", headers=headers())
        current.raise_for_status()
        trip_version = current.json()["version"]
        activated = client.post(
            f"/v1/trips/{trip_id}/plans/{plan_id}:activate",
            headers={**headers(), "If-Match": str(trip_version)},
        )
        activated.raise_for_status()
        check(activated.json()["status"] == "ACTIVE", "计划已激活")

        print("6) 现场记录 + 离线同步")
        record = client.post(
            f"/v1/visits/{visit_id}/records",
            headers=headers(),
            json={
                "client_record_id": str(uuid.uuid4()),
                "outcome": "MET",
                "notes": "E2E：客户确认续约意向，要求两周内报价",
                "client_updated_at": datetime.now(UTC).isoformat(),
                "photos": [],
                "follow_up_tasks": [{"title": "两周内提交报价", "due_date": "2026-11-20"}],
            },
        )
        record.raise_for_status()
        check(record.json()["applied"] is True, "现场记录已落库")
        record_id = record.json()["id"]

        sync_client_id = str(uuid.uuid4())
        sync_at = datetime.now(UTC) + timedelta(minutes=1)
        synced = client.post(
            "/v1/sync:push",
            headers=headers(),
            json={
                "device_id": "at-e2e-device",
                "items": [
                    {
                        "item_id": sync_client_id,
                        "entity": "visit_record",
                        "operation": "upsert",
                        "payload": {
                            "visit_id": visit_id,
                            "client_record_id": sync_client_id,
                            "outcome": "MET",
                            "notes": "E2E：离线队列补传的补充记录",
                            "client_updated_at": sync_at.isoformat(),
                            "photos": [],
                            "follow_up_tasks": [],
                        },
                        "client_updated_at": sync_at.isoformat(),
                    }
                ],
            },
        )
        synced.raise_for_status()
        sync_body = synced.json()
        check(sync_body["summary"]["applied"] == 1, "离线同步逐项 applied")

        print("6b) CRM 回写（异步任务 + 文件适配器）")
        crm = client.post(f"/v1/visit-records/{record_id}/crm-sync", headers=headers())
        crm.raise_for_status()
        crm_id = crm.json()["id"]
        check(crm.json()["status"] == "QUEUED", "CRM 同步已排队")
        run_worker_once()
        crm_detail = client.get(f"/v1/crm-sync-jobs/{crm_id}", headers=headers())
        crm_detail.raise_for_status()
        check(crm_detail.json()["status"] == "PREPARED", "CRM 适配器生成待导入文件")
        external = (crm_detail.json().get("result") or {}).get("external_id", "")
        if external.startswith("file:"):
            Path(external.removeprefix("file:")).unlink(missing_ok=True)

        print("6c) 显式汇率与预算折算")
        expense = client.post(
            f"/v1/trips/{trip_id}/expenses",
            headers=headers(),
            json={
                "category": "MEALS",
                "amount": "10.00",
                "currency": "USD",
                "occurred_on": datetime.now(UTC).date().isoformat(),
            },
        )
        expense.raise_for_status()
        rate = client.post(
            "/v1/fx-rates",
            headers=headers(),
            json={
                "base_currency": "EUR",
                "quote_currency": "USD",
                "rate": "1.25",
                "as_of": datetime.now(UTC).isoformat(),
                "source": f"e2e:{trip_id}",
            },
        )
        rate.raise_for_status()
        budget = client.get(
            f"/v1/trips/{trip_id}/budget",
            headers=headers(),
            params={"base_currency": "EUR"},
        )
        budget.raise_for_status()
        converted = budget.json()["converted_actual_total"]
        check(converted is not None and float(converted) == 8.0, "按显式汇率折算实际费用")

        print("7) 会议纪要草稿（LLM 网关）")
        minutes = client.post(f"/v1/visits/{visit_id}/minutes:draft", headers=headers(), json={})
        minutes.raise_for_status()
        minutes_body = minutes.json()
        check(bool(minutes_body["summary"]), "纪要摘要非空")
        check(bool(minutes_body["llm_invocation_id"]), "纪要可回溯 LLM 调用")

        print("8) 完成出行 → 复盘记忆 → 指标 → 审计")
        completed = client.patch(
            f"/v1/trips/{trip_id}", headers=headers(), json={"status": "COMPLETED"}
        )
        completed.raise_for_status()
        review = client.post(f"/v1/trips/{trip_id}/memory:review", headers=headers())
        review.raise_for_status()
        check(len(review.json()["summaries"]) == 1, "复盘摘要已生成")

        metrics = client.get("/v1/metrics/summary", headers=headers())
        metrics.raise_for_status()
        metric_map = {item["key"]: item for item in metrics.json()["metrics"]}
        check(len(metric_map) == 9, "指标树 9 项可读")
        check("planning.validator_pass_rate" in metric_map, "规划质量指标存在")

        audit = client.get(f"/v1/trips/{trip_id}/audit", headers=headers())
        audit.raise_for_status()
        kinds = {item["kind"] for item in audit.json()}
        check({"outbox", "plan_attempt", "visit_record"} <= kinds, "审计时间线包含关键事实")

    print("\n全部端到端检查通过。")
    return 0


def _cleanup_created() -> None:
    trip_id = _CREATED.get("trip")
    place_id = _CREATED.get("place")
    if trip_id and place_id:
        cleanup(trip_id, place_id)
        print("已清理本次脚本数据。")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CheckFailed as exc:
        print(f"\n验收失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except httpx.HTTPStatusError as exc:
        print(
            f"\nHTTP 失败：{exc.request.method} {exc.request.url} -> "
            f"{exc.response.status_code} {exc.response.text[:500]}",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    finally:
        _cleanup_created()
