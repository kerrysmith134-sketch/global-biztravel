#!/usr/bin/env python
"""演示种子（TestFlight 测试用）：一套"像一个真实用户在用"的数据。

与 seed_dev.py（身份/夹具）互补：本脚本**走真实 API**造业务数据，让审批、
幂等、主动式扫描等规则真实生效——不直插业务表（RLS/状态机绕不过也不该绕）。

幂等：行程按名字去重；费用/报销/记录用固定幂等键；重跑不会翻倍。

造出来的世界（以今天为基准）：
- 西门子（慕尼黑）/ ASML（阿姆斯特丹）/ 星展银行（新加坡）/ 三菱 UFJ（东京）/ 大众（柏林）
- 「进行中」柏林+阿姆：已生成并激活计划
- 「T-2 天出发」新加坡：触发签证核验建议（Worker 扫描）+ 天气拉取
- 「T-3 天出发」东京：第二个签证建议（不同国家）
- 「已完成」慕尼黑（上上周）：3 拜访 + 3 现场记录 + 1 条纪要任务 + 5 条费用 + 1 张待审批报销单
- 汇率：Frankfurter 真实行情同步（EUR 基准）

用法：.venv/bin/python scripts/seed_demo.py [--api http://localhost:8080]
"""

from __future__ import annotations

import argparse
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib import request as urlrequest

ORG = "11111111-1111-1111-1111-111111111111"
USER = "22222222-2222-2222-2222-222222222222"

CUSTOMERS = [
    {"name": "大众汽车（柏林总部）", "city": "Berlin", "lat": 52.5208, "lon": 13.3702, "country": "DE"},
    {"name": "西门子（慕尼黑园区）", "city": "Munich", "lat": 48.1391, "lon": 11.5802, "country": "DE"},
    {"name": "ASML（费尔德霍温园区）", "city": "Eindhoven", "lat": 51.4416, "lon": 5.4697, "country": "NL"},
    {"name": "星展银行（新加坡滨海湾）", "city": "Singapore", "lat": 1.2830, "lon": 103.8600, "country": "SG"},
    {"name": "三菱 UFJ（东京丸之内）", "city": "Tokyo", "lat": 35.6812, "lon": 139.7671, "country": "JP"},
]


def http(method: str, path: str, body: dict | None = None, *, key: str | None = None):
    headers = {
        "X-Org-Id": ORG,
        "X-User-Id": USER,
        "Content-Type": "application/json",
    }
    if key:
        headers["Idempotency-Key"] = key
    data = None if body is None else json_dumps(body).encode()
    req = urlrequest.Request(f"{BASE}{path}", data=data, method=method, headers=headers)
    try:
        with urlrequest.urlopen(req, timeout=30) as response:
            payload = response.read()
            return response.status, json_loads(payload) if payload else None
    except Exception as exc:  # noqa: BLE001 —— 打印状态便于排障
        body_text = getattr(exc, "read", lambda: b"")()
        print(f"  [ERR] {method} {path}: {exc} {body_text[:200]}")
        raise


def json_dumps(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


def json_loads(raw: bytes):
    import json

    return json.loads(raw)


def dt_local(days: int, hour: int, minute: int = 0) -> str:
    moment = datetime.now(UTC).astimezone() + timedelta(days=days)
    return moment.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


def main() -> int:
    global BASE
    parser = argparse.ArgumentParser(description="演示种子（走真实 API）")
    parser.add_argument("--api", default="http://localhost:8080")
    args = parser.parse_args()
    BASE = args.api.rstrip("/")
    now = datetime.now(UTC)

    print(f"== 演示种子 → {BASE} ==")

    # 1) 客户 + 地点（客户先建，地点挂客户）
    place_ids: dict[str, str] = {}
    existing_places = http("GET", "/v1/places?limit=100")[1]
    known_names = {item["name"] for item in existing_places.get("items", existing_places if isinstance(existing_places, list) else [])}
    for spec in CUSTOMERS:
        if spec["name"] in known_names:
            match = [item for item in existing_places.get("items", existing_places if isinstance(existing_places, list) else []) if item["name"] == spec["name"]]
            if match:
                place_ids[spec["city"]] = match[0]["id"]
                print(f"  地点已存在：{spec['name']}")
                continue
        status, place = http("POST", "/v1/places", {
            "name": spec["name"],
            "city": spec["city"],
            "country_code": spec["country"],
            "lat": spec["lat"],
            "lon": spec["lon"],
            "customer_name": spec["name"],
        })
        assert status in (200, 201), place
        place_ids[spec["city"]] = place["id"]
        print(f"  地点：{spec['name']}")

    def trip_id_by_name(name: str) -> str | None:
        status, page = http("GET", "/v1/trips?limit=100")
        items = page.get("items", [])
        for item in items:
            if item["name"] == name:
                return item["id"]
        return None

    def ensure_trip(name: str, days_from: int, days_to: int, countries: list[str], visits: list[tuple[str, str]], origin_city: str | None = None) -> str:
        existing = trip_id_by_name(name)
        if existing is not None:
            print(f"  行程已存在：{name}")
            return existing
        payload = {
            "name": name,
            "purpose": "客户拜访与商务谈判",
            "countries": countries,
            "window_start": dt_local(days_from, 9),
            "window_end": dt_local(days_to, 18),
            "budget_amount": "6000.00",
            "budget_currency": "EUR",
            "currency": "EUR",
        }
        if origin_city:
            payload["origin_place_id"] = place_ids[origin_city]
        status, trip = http("POST", "/v1/trips", payload)
        assert status == 201, trip
        trip_id = trip["id"]
        for title, city in visits:
            v_status, visit = http("POST", f"/v1/trips/{trip_id}/visits", {
                "place_id": place_ids[city],
                "title": title,
                "service_minutes": 90,
                "priority": 1,
            })
            assert v_status == 201, visit
        print(f"  行程：{name}（{len(visits)} 个拜访）")
        return trip_id

    # 2) 四个行程
    trip_active = ensure_trip(
        "柏林—阿姆斯特丹客户周（进行中）", -1, 2, ["DE", "NL"],
        [("大众总部年度复审", "Berlin"), ("ASML 产线对接", "Eindhoven")],
        origin_city="Berlin",
    )
    trip_sg = ensure_trip(
        "新加坡星展银行洽谈（即将出发）", 2, 5, ["SG"],
        [("星展银行 SMB 产品推介", "Singapore")],
    )
    trip_jp = ensure_trip(
        "东京三菱 UFJ 年度会谈（即将出发）", 3, 6, ["JP"],
        [("三菱 UFJ 合作框架会谈", "Tokyo")],
    )
    trip_done = ensure_trip(
        "慕尼黑西门子季度访问（已完成）", -14, -11, ["DE"],
        [("西门子季度例会", "Munich"), ("园区参观与产线评估", "Munich"), ("采购部专题会", "Munich")],
    )

    # 3) 进行中行程：生成 + 激活计划（真实规划链路）
    status, plans = http("GET", f"/v1/trips/{trip_active}/plans")
    plan_items = plans if isinstance(plans, list) else plans.get("items", [])
    has_active = any(p.get("status") == "ACTIVE" for p in plan_items)
    if not has_active:
        g_status, accepted = http("POST", f"/v1/trips/{trip_active}/plans:generate", {})
        assert g_status == 202, accepted
        job_id = accepted["job_id"]
        for _ in range(30):
            status, job = http("GET", f"/v1/jobs/{job_id}")
            if job["status"] in ("SUCCEEDED", "FAILED", "DEAD"):
                break
            import time

            time.sleep(1)
        assert job["status"] == "SUCCEEDED", job
        plan_id = job["result"]["plan_version_id"]
        a_status, activated = http("POST", f"/v1/trips/{trip_active}/plans/{plan_id}:activate", {})
        assert a_status in (200, 201), activated
        print(f"  进行中行程已激活计划：{plan_id[:8]}…")
    else:
        print("  进行中行程已有激活计划")

    # 4) 已完成行程：现场记录 + 纪要任务 + 费用 + 报销单
    status, visits_page = http("GET", f"/v1/trips/{trip_done}/visits")
    done_visits = visits_page["items"] if isinstance(visits_page, dict) else visits_page
    outcomes = [
        ("MET", "季度 OKR 全部达成；确认下季度追加两个产线项目。", "签约意向确认"),
        ("MET", "产线自动化程度高于预期，列入年度合作白名单。", "产线评估报告"),
        ("RESCHEDULED", "采购部流程调整，专题会改到下月；拿到对接人名单。", "采购对接人清单"),
    ]
    for visit, (outcome, notes, follow) in zip(done_visits[:3], outcomes):
        v_id = visit["id"]
        try:
            r_status, record = http("POST", f"/v1/visits/{v_id}/records", {
                "client_record_id": f"demo-record-{v_id[:8]}",
                "outcome": outcome,
                "notes": notes,
                "follow_up_tasks": [{"title": follow, "due_date": dt_local(7, 18)[:10]}],
                "client_updated_at": dt_local(-11, 17),
            })
            assert r_status == 201, record
        except Exception:
            print("  记录已存在，跳过")  # 固定 client_record_id 幂等
    print(f"  已完成行程现场记录：{min(len(done_visits), 3)} 条")

    first_visit = done_visits[0]["id"] if done_visits else None
    if first_visit:
        m_status, minutes = http("POST", f"/v1/visits/{first_visit}/minutes:jobs", {})
        if m_status in (200, 202):
            print("  纪要任务已入队（stub LLM 模板产出）")

    expenses = [
        ("MEALS", "42.50", "EUR", -13, "客户午餐（西门子园区）"),
        ("TRANSPORT", "89.00", "EUR", -13, "慕尼黑市内铁路 3 日票 + 出租"),
        ("LODGING", "268.00", "EUR", -12, "万豪慕尼黑两晚"),
        ("TRANSPORT", "56.30", "USD", -12, "机场快线（App 内购，美元扣款）"),
        ("OTHER", "300.00", "CNY", -11, "伴手礼（签约客户）"),
    ]
    for i, (category, amount, currency, day, note) in enumerate(expenses):
        e_status, _ = http("POST", f"/v1/trips/{trip_done}/expenses", {
            "category": category,
            "amount": amount,
            "currency": currency,
            "occurred_on": dt_local(day, 12)[:10],
            "note": note,
        }, key=f"demo-expense-{i}")
    print(f"  费用：{len(expenses)} 条（EUR/USD/CNY）")

    status, budget = http("GET", f"/v1/trips/{trip_done}/budget?base_currency=EUR")
    print(f"  预算折算：complete={budget.get('conversion_complete')}")

    r_status, reports = http("GET", f"/v1/trips/{trip_done}/expense-reports")
    existing_reports = reports if isinstance(reports, list) else reports.get("items", [])
    if not existing_reports:
        r_status, report = http("POST", f"/v1/trips/{trip_done}/expense-reports", {
            "expense_ids": [],
            "note": "慕尼黑季度访问全部费用",
        }, key="demo-expense-report-1")
        if r_status in (200, 201):
            report_id = report["id"]
            http("POST", f"/v1/expense-reports/{report_id}:submit", {})
            print("  报销单已提交（行动中心 → 待审批）")
    else:
        print("  报销单已存在")

    # 5) 汇率：真实行情同步（Frankfurter）
    s_status, sync = http("POST", "/v1/fx-rates:sync", {
        "base_currency": "EUR",
        "symbols": ["USD", "CNY", "GBP", "SGD", "JPY"],
    }, key=f"demo-fx-sync-{now.date().isoformat()}")
    print(f"  FX 同步入队：job={sync['job_id'][:8]}…" if s_status == 202 else f"  FX 同步：{s_status}")

    # 6) 即将出发的行程：主动拉一次天气（恶劣与否都真实可见）
    w_status, weather = http("POST", "/v1/integrations/signals:pull-weather", {"trip_id": trip_sg})
    if w_status == 200:
        items = weather.get("items", [])
        print(f"  新加坡天气：{[(i.get('temperature_c'), i.get('wind_kmh')) for i in items]}")

    print("== 完成。Worker 每分钟扫描，1–2 分钟后『主动建议』出现签证核验提醒 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
