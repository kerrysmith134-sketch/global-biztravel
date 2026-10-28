"""后端核心闭环的**真实数据库**冒烟测试（设计文档 v2.0 第 4、5、6 章）。

这不是单元测试——它把 FastAPI 应用挂到真实的 PostgreSQL/PostGIS 上（容器 ``zhi-tu-pg``，
端口 55432），走完整 HTTP 栈：中间件 → 依赖（身份头 → membership → 租户 GUC 会话）→
路由 → 服务层 → ORM → RLS 生效的数据库。用例刻意去验证那些**只有真库才能暴露**的行为：

- 租户隔离真的在数据库层拦住了跨租户读取（AT-S01）；
- 幂等键重放返回与首答**逐字节相同**的响应，并带 ``Idempotent-Replay`` 头；
- Excel 两阶段导入：``parse`` 不写任何业务数据，``confirm`` 落库且**只能确认一次**；
- 乐观并发令牌（``If-Match``）在版本不符时返回 412。

运行前置：``scripts/seed_dev.py`` 已跑过（身份、两个组织、出行与拜访、导入夹具都在）。

    .venv/bin/python -m pytest services/api/tests/test_backend_smoke.py -v -m integration

断言一律写成"跨次运行稳定"的形式（文件内在计数写死、会随运行累积的计数写 ``>=``），
因此重复执行本文件不会因为上一次留下的数据而失败。

**测试卫生**：本文件打的是共享的开发库，所以凡是写入业务数据的用例都自己收尾——
``confirm`` 用例结束后会删掉本次导入产生的拜访（见 ``drop_visits``）。
唯一会持续累积的是"创建出行"类用例留下的少量一次性出行：设计上出行是"取消"而不是
"删除"，对外没有删除接口，因此这里不做清理；它们不影响任何断言的稳定性。
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.trip import ImportBatch, Trip
from fastapi.testclient import TestClient
from sqlalchemy import func, select

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "docs" / "acceptance-tests" / "fixtures" / "sample-customers.xlsx"

ORG_A = "11111111-1111-1111-1111-111111111111"  # 安纳瑞能源
ORG_B = "33333333-3333-3333-3333-333333333333"  # Northwind Trading
USER_A = "22222222-2222-2222-2222-222222222222"  # kerry@anari.example（OWNER）
USER_A2 = "55555555-5555-5555-5555-555555555555"  # mia@anari.example（ADMIN）
USER_B = "44444444-4444-4444-4444-444444444444"  # bella@northwind.example（仅 ORG_B 成员）

TRIP_A_NAME = "2026 Q4 欧洲多国商务出行"
TRIP_B_NAME = "2026 东南亚供应商走访"

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(org: str, user: str) -> dict[str, str]:
    return {"X-Org-Id": org, "X-User-Id": user}


def all_trips(client: TestClient, hdrs: dict[str, str]) -> list[dict]:
    """翻完所有分页。

    共享开发库里出行会随每次跑测试累积，而列表按 ``window_start`` 倒序分页——只读第一页会
    让"种子出行"在累积到一页以上之后凭空消失。跨次稳定的写法只能是走完全部分页。
    """
    items: list[dict] = []
    cursor: str | None = None
    while True:
        params = {"cursor": cursor} if cursor else None
        resp = client.get("/v1/trips", headers=hdrs, params=params)
        assert resp.status_code == 200, resp.text
        page = resp.json()
        items.extend(page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            return items


def trip_id_by_name(client: TestClient, hdrs: dict[str, str], name: str) -> str:
    for item in all_trips(client, hdrs):
        if item["name"] == name:
            return item["id"]
    raise AssertionError(f"未找到出行 {name!r}；请先运行 scripts/seed_dev.py")


def visit_ids(client: TestClient, hdrs: dict[str, str], trip_id: str) -> set[str]:
    resp = client.get(f"/v1/trips/{trip_id}/visits", headers=hdrs)
    assert resp.status_code == 200, resp.text
    return {item["id"] for item in resp.json()}


def count_trips(org: str) -> int:
    """直接数库里的出行行数。

    为什么不能只看 API：``GET /v1/trips`` 是分页的，共享开发库里出行会因为反复运行而累积，
    分页上限会把"多出来的一条"吃掉。幂等要防的恰恰是"库里多了一份"，所以必须落到库上数。
    """
    with org_session(UUID(org)) as session:
        return session.scalar(select(func.count()).select_from(Trip)) or 0


def count_import_batches(org: str) -> int:
    """数导入批次行数——``parse`` 重放不该多出第二批 ``ImportBatch``。"""
    with org_session(UUID(org)) as session:
        return session.scalar(select(func.count()).select_from(ImportBatch)) or 0


def drop_visits(client: TestClient, hdrs: dict[str, str], ids: set[str]) -> None:
    """删掉本用例自己造出来的拜访，让开发库回到基线。

    集成测试打的是共享的开发库。凡是用例会写入业务数据的，都必须自己收尾；
    否则每跑一次测试，出行里就多出几行"测试残留"，验收报告与种子基线都会漂移。
    出行没有删除接口（业务上出行是"取消"而不是"删除"），所以本文件里创建出行的用例
    只留下少量确定性的一次性出行，这点已在模块 docstring 里说明。
    """
    for visit_id in sorted(ids):
        resp = client.delete(f"/v1/visits/{visit_id}", headers=hdrs)
        assert resp.status_code == 200, resp.text


# ------------------------------------------------------------------ 健康检查
def test_health_reports_database_up(client: TestClient) -> None:
    resp = client.get("/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "up"
    # 探针不该要求身份头：容器编排没有业务身份。
    assert "X-Trace-Id" in resp.headers


# --------------------------------------------------- 幂等：同键重放字节一致
def test_create_trip_is_idempotent_under_same_key(client: TestClient) -> None:
    hdrs = {**headers(ORG_A, USER_A), "Idempotency-Key": "smoke-trip-create-v1"}
    payload = {
        "name": "冒烟测试·幂等出行",
        "window_start": "2027-03-01T00:00:00+08:00",
        "window_end": "2027-03-05T00:00:00+08:00",
        "countries": ["de"],
        "currency": "EUR",
    }
    first = client.post("/v1/trips", headers=hdrs, json=payload)
    trips_after_first = count_trips(ORG_A)
    second = client.post("/v1/trips", headers=hdrs, json=payload)

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    # 核心不变式：重放返回与首答**逐字节相同**的 body。
    assert first.content == second.content
    assert second.headers.get("Idempotent-Replay") == "true"

    # 更核心的不变式：重放**没有第二次副作用**。
    # 只看响应的幂等实现（先执行业务、后写记录）会给出"响应一模一样、库里却多一条"的假象——
    # 这条断言才是真正守着它的那道门。
    assert count_trips(ORG_A) == trips_after_first

    trip = first.json()
    assert trip["status"] == "DRAFT"
    assert trip["version"] == 1
    assert trip["countries"] == ["DE"]  # 国家码统一大写
    assert trip["visit_count"] == 0

    # 列表可见，且分页壳形状正确。
    listed = client.get("/v1/trips", headers=headers(ORG_A, USER_A))
    assert listed.status_code == 200
    page = listed.json()
    assert set(page.keys()) == {"items", "next_cursor"}
    assert trip["id"] in {item["id"] for item in all_trips(client, headers(ORG_A, USER_A))}


# ------------------------------------------- 不同键但同内容：应当真的再建一次
def test_create_trip_without_key_is_not_deduped(client: TestClient) -> None:
    hdrs = headers(ORG_A, USER_A)
    payload = {
        "name": "冒烟测试·无幂等键",
        "window_start": "2027-04-01T00:00:00+08:00",
        "window_end": "2027-04-02T00:00:00+08:00",
    }
    a = client.post("/v1/trips", headers=hdrs, json=payload)
    b = client.post("/v1/trips", headers=hdrs, json=payload)
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] != b.json()["id"]
    # 不带键就没有重放标记——幂等只在带键时被承诺。
    assert b.headers.get("Idempotent-Replay") is None


# ------------------------------------------------- Excel 两阶段导入（parse）
def test_parse_detects_every_intended_problem_code(client: TestClient) -> None:
    hdrs = headers(ORG_A, USER_A)
    trip_id = trip_id_by_name(client, hdrs, TRIP_A_NAME)
    data = FIXTURE.read_bytes()

    customers_before = len(client.get("/v1/customers", headers=hdrs).json()["items"])
    places_before = len(client.get("/v1/places", headers=hdrs).json()["items"])
    visits_before = len(client.get(f"/v1/trips/{trip_id}/visits", headers=hdrs).json())

    resp = client.post(
        f"/v1/trips/{trip_id}/imports:parse",
        headers=hdrs,
        files={"file": ("sample-customers.xlsx", data, XLSX_MIME)},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # 文件内在计数（与运行次数无关）：10 行数据，其中 1 行阻断、1 行批内重复。
    assert body["total_rows"] == 10
    assert body["invalid_rows"] == 1
    assert body["duplicate_rows"] == 1
    assert body["valid_rows"] == 8
    assert body["status"] == "PARSED"

    # 未识别列被点名，且不会把尾部空列算进去。
    assert body["unknown_columns"] == ["备注"]
    assert body["missing_columns"] == []

    codes = {issue["code"] for issue in body["report"]["issues"]}
    expected = {
        "MISSING_CUSTOMER_NAME",
        "COORDINATES_PARTIAL",
        "COORDINATES_MISSING",
        "INVALID_SERVICE_MINUTES",
        "INVALID_TIME_WINDOW",
        "INVALID_COUNTRY_CODE",
        "INVALID_EMAIL",
        "DUPLICATE_ROW",
        "UNKNOWN_COLUMNS",
    }
    assert expected <= codes, f"缺失问题码：{expected - codes}"

    # 阻断性问题的分级必须正确：只有缺客户名是 ERROR。
    severities = {issue["code"]: issue["severity"] for issue in body["report"]["issues"]}
    assert severities["MISSING_CUSTOMER_NAME"] == "ERROR"
    assert severities["INVALID_EMAIL"] == "WARNING"

    # 逐行预览里能看到"哪些行有问题"，客户端据此渲染勾选表。
    rows = {row["row_number"]: row for row in body["rows"]}
    assert rows[7]["status"] == "INVALID"
    assert "MISSING_CUSTOMER_NAME" in {i["code"] for i in rows[7]["issues"]}
    assert rows[6]["status"] == "DUPLICATE"
    assert rows[5]["status"] == "VALID"
    assert "COORDINATES_PARTIAL" in {i["code"] for i in rows[5]["issues"]}

    # 城市索引补齐坐标：Berlin 那行没有经纬度，但离线索引认识它。
    assert rows[3]["normalized"]["latitude"] is not None
    assert rows[3]["normalized"]["geocode_status"] == "RESOLVED"
    # 无法解析的城市（Atlantis）保留 MISSING。
    assert rows[10]["normalized"]["geocode_status"] == "MISSING"

    # parse 是只读的：业务数据一个字节都没写。客户/地点/拜访计数在解析前后完全不变。
    assert len(client.get("/v1/customers", headers=hdrs).json()["items"]) == customers_before
    assert len(client.get("/v1/places", headers=hdrs).json()["items"]) == places_before
    assert len(client.get(f"/v1/trips/{trip_id}/visits", headers=hdrs).json()) == visits_before


def test_parse_replay_under_key_does_not_create_second_batch(client: TestClient) -> None:
    # 键派生自文件内容：同一份夹具反复运行稳定命中同一条幂等记录（于是首次调用本身就是重放），
    # 夹具字节一旦变化又自动换键。写死字符串会让"换了夹具的旧记录"把首次调用打成 409
    # ——那是 key 复用误报，不是幂等失效（幂等记录的 TTL 只有 24h）。
    data = FIXTURE.read_bytes()
    hdrs = {
        **headers(ORG_A, USER_A),
        "Idempotency-Key": f"smoke-parse-{hashlib.sha256(data).hexdigest()[:12]}",
    }
    trip_id = trip_id_by_name(client, headers(ORG_A, USER_A), TRIP_A_NAME)

    first = client.post(
        f"/v1/trips/{trip_id}/imports:parse",
        headers=hdrs,
        files={"file": ("sample-customers.xlsx", data, XLSX_MIME)},
    )
    batches_after_first = count_import_batches(ORG_A)
    second = client.post(
        f"/v1/trips/{trip_id}/imports:parse",
        headers=hdrs,
        files={"file": ("sample-customers.xlsx", data, XLSX_MIME)},
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["import_id"] == second.json()["import_id"]
    assert first.content == second.content
    assert second.headers.get("Idempotent-Replay") == "true"

    # 重传同一文件不该在库里再落一批 ImportBatch（同批次的 ImportRow 更不该翻倍）。
    assert count_import_batches(ORG_A) == batches_after_first

    # 刷新预览页走只读 GET，与 parse 首答是同一份读模型。
    preview = client.get(f"/v1/imports/{first.json()['import_id']}", headers=headers(ORG_A, USER_A))
    assert preview.status_code == 200
    assert preview.json()["total_rows"] == 10


# ------------------------------------------ Excel 两阶段导入（confirm 落库）
def test_confirm_import_persists_and_cannot_run_twice(client: TestClient) -> None:
    hdrs = headers(ORG_A, USER_A)
    trip_id = trip_id_by_name(client, hdrs, TRIP_A_NAME)
    before = visit_ids(client, hdrs, trip_id)

    # 用唯一文件名，保证本次一定拿到一个 **PARSED** 的新批次（同文件同键会命中上一次的重放）。
    filename = f"sample-customers-{uuid.uuid4().hex[:8]}.xlsx"
    parsed = client.post(
        f"/v1/trips/{trip_id}/imports:parse",
        headers=hdrs,
        files={"file": (filename, FIXTURE.read_bytes(), XLSX_MIME)},
    )
    assert parsed.status_code == 201, parsed.text
    import_id = parsed.json()["import_id"]
    valid_rows = [
        row["row_number"] for row in parsed.json()["rows"] if row["status"] == "VALID"
    ]
    assert valid_rows == [2, 3, 4, 5, 8, 9, 10, 11]

    # 确认带上唯一幂等键：客户端真正的重试（同键同体）必须能安全重发。
    key = f"smoke-confirm-{uuid.uuid4().hex[:12]}"
    keyed = {**hdrs, "Idempotency-Key": key}
    confirm_body = {"import_id": import_id, "accepted_row_numbers": valid_rows}
    confirmed = client.post(f"/v1/trips/{trip_id}/imports:confirm", headers=keyed, json=confirm_body)
    assert confirmed.status_code == 200, confirmed.text
    result = confirmed.json()
    assert result["status"] == "CONFIRMED"
    assert result["accepted_rows"] == 8
    assert result["rejected_rows"] == 0
    assert result["created_visits"] == 8
    assert result["created_customers"] + result["reused_customers"] >= 8
    # 缺坐标的那一行必须被明确警示（它不会进入路线优化）。
    assert any("坐标缺失" in w for w in result["warnings"])

    # 落库确实发生了：新增的恰好是这 8 行，且都能按 id 认出来。
    after = visit_ids(client, hdrs, trip_id)
    created = after - before
    assert len(created) == 8

    # 幂等重放：同键再发一次 → 直接回放首答，**不再建第二批客户/地点/拜访**。
    # 这一条正是"先执行业务、后写幂等记录"这种实现会漏掉的：它会让响应看起来一致，
    # 而库里多出 8 条拜访（第二次确认本应 409，但重放根本不该走到那一层）。
    replay = client.post(f"/v1/trips/{trip_id}/imports:confirm", headers=keyed, json=confirm_body)
    assert replay.status_code == 200, replay.text
    assert replay.content == confirmed.content
    assert replay.headers.get("Idempotent-Replay") == "true"
    assert visit_ids(client, hdrs, trip_id) == after

    # 不带幂等键的重复确认是另一次真实请求：落库不是幂等操作，必须被拒绝而不是静默再来一遍。
    again = client.post(f"/v1/trips/{trip_id}/imports:confirm", headers=hdrs, json=confirm_body)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "IMPORT_ALREADY_CONFIRMED"

    # 收尾：删掉本次导入产生的拜访，恢复基线。
    # 二次确认被拒这一点与清理无关——它读的是批次的 CONFIRMED 状态。
    drop_visits(client, hdrs, created)
    assert visit_ids(client, hdrs, trip_id) == before


def test_confirm_rejects_blocking_rows_when_explicitly_selected(client: TestClient) -> None:
    hdrs = headers(ORG_A, USER_A)
    trip_id = trip_id_by_name(client, hdrs, TRIP_A_NAME)
    before = visit_ids(client, hdrs, trip_id)
    filename = f"sample-customers-{uuid.uuid4().hex[:8]}.xlsx"
    parsed = client.post(
        f"/v1/trips/{trip_id}/imports:parse",
        headers=hdrs,
        files={"file": (filename, FIXTURE.read_bytes(), XLSX_MIME)},
    )
    assert parsed.status_code == 201
    import_id = parsed.json()["import_id"]

    # 用户硬把"缺客户名"的第 7 行也勾了：服务端必须拒绝该行并给出原因，其余行照常入库。
    picked = [7, 11]
    resp = client.post(
        f"/v1/trips/{trip_id}/imports:confirm",
        headers=hdrs,
        json={"import_id": import_id, "accepted_row_numbers": picked},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["rejected_rows"] == 1
    assert result["accepted_rows"] == 1
    assert any("第 7 行" in w for w in result["warnings"])

    created = visit_ids(client, hdrs, trip_id) - before
    assert len(created) == 1  # 只落了第 11 行
    drop_visits(client, hdrs, created)
    assert visit_ids(client, hdrs, trip_id) == before


def test_parse_rejects_non_excel_upload(client: TestClient) -> None:
    hdrs = headers(ORG_A, USER_A)
    trip_id = trip_id_by_name(client, hdrs, TRIP_A_NAME)
    resp = client.post(
        f"/v1/trips/{trip_id}/imports:parse",
        headers=hdrs,
        files={"file": ("legacy.xls", b"not really a workbook", "application/vnd.ms-excel")},
    )
    assert resp.status_code == 415
    assert resp.json()["error"]["code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_parse_missing_required_column_is_422(client: TestClient) -> None:
    from io import BytesIO

    from openpyxl import Workbook

    hdrs = headers(ORG_A, USER_A)
    trip_id = trip_id_by_name(client, hdrs, TRIP_A_NAME)

    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["城市", "国家"])  # 没有"客户名称"这一必需列
    sheet.append(["Berlin", "DE"])
    buffer = BytesIO()
    workbook.save(buffer)

    resp = client.post(
        f"/v1/trips/{trip_id}/imports:parse",
        headers=hdrs,
        files={"file": ("no-name.xlsx", buffer.getvalue(), XLSX_MIME)},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "MISSING_COLUMN"


# ---------------------------------------------------------- 租户隔离 AT-S01
def test_cross_tenant_read_is_blocked(client: TestClient) -> None:
    trip_a = trip_id_by_name(client, headers(ORG_A, USER_A), TRIP_A_NAME)

    # ORG_B 直接按 id 请求 ORG_A 的出行 → 404（不是 403：连"存在"都不该暴露）。
    resp = client.get(f"/v1/trips/{trip_a}", headers=headers(ORG_B, USER_B))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"

    # ORG_B 的列表里不含 ORG_A 的任何出行。
    listing = client.get("/v1/trips", headers=headers(ORG_B, USER_B))
    assert listing.status_code == 200
    assert trip_a not in {item["id"] for item in listing.json()["items"]}
    assert TRIP_A_NAME not in {item["name"] for item in listing.json()["items"]}
    # 反向亦然：ORG_A 看不到 ORG_B 的出行。
    trip_b = trip_id_by_name(client, headers(ORG_B, USER_B), TRIP_B_NAME)
    listing_a = client.get("/v1/trips", headers=headers(ORG_A, USER_A))
    assert trip_b not in {item["id"] for item in listing_a.json()["items"]}

    # 导入批次也一样隔离：ORG_B 读 ORG_A 的批次 → 404。
    parsed = client.post(
        f"/v1/trips/{trip_a}/imports:parse",
        headers=headers(ORG_A, USER_A),
        files={"file": (f"iso-{uuid.uuid4().hex[:8]}.xlsx", FIXTURE.read_bytes(), XLSX_MIME)},
    )
    assert parsed.status_code == 201
    stolen = client.get(f"/v1/imports/{parsed.json()['import_id']}", headers=headers(ORG_B, USER_B))
    assert stolen.status_code == 404


# -------------------------------------------------------- 权限：角色与成员资格
def test_non_member_is_forbidden(client: TestClient) -> None:
    # 带上 ORG_A 的组织头 + 只在 ORG_B 有 membership 的用户 → 403。
    resp = client.get("/v1/trips", headers=headers(ORG_A, USER_B))
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


def test_admin_role_can_travel(client: TestClient) -> None:
    # ADMIN（rank 2）≥ TRAVELER（rank 1）：能建出行。
    resp = client.post(
        "/v1/trips",
        headers=headers(ORG_A, USER_A2),
        json={
            "name": "冒烟测试·管理员建单",
            "window_start": "2027-05-01T00:00:00+08:00",
            "window_end": "2027-05-02T00:00:00+08:00",
        },
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["owner_user_id"] == USER_A2


# ------------------------------------------- 乐观并发：If-Match 版本不符 412
def test_if_match_optimistic_locking(client: TestClient) -> None:
    hdrs = headers(ORG_A, USER_A)
    created = client.post(
        "/v1/trips",
        headers=hdrs,
        json={
            "name": "冒烟测试·并发令牌",
            "window_start": "2027-06-01T00:00:00+08:00",
            "window_end": "2027-06-02T00:00:00+08:00",
        },
    )
    assert created.status_code == 201
    trip = created.json()
    assert trip["version"] == 1

    ok = client.patch(
        f"/v1/trips/{trip['id']}",
        headers={**hdrs, "If-Match": f'W/"{trip["version"]}"'},
        json={"purpose": "客户拜访与产线审核"},
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["version"] == 2  # 改动次数作为并发令牌递增

    stale = client.patch(
        f"/v1/trips/{trip['id']}",
        headers={**hdrs, "If-Match": '"1"'},  # 用过期版本再改
        json={"purpose": "基于旧版本的重写"},
    )
    assert stale.status_code == 412
    assert stale.json()["error"]["code"] == "PRECONDITION_FAILED"

    # 客户端不能直接把出行置为 ACTIVE——生效只能经由"激活计划"。
    wrong_status = client.patch(
        f"/v1/trips/{trip['id']}",
        headers=hdrs,
        json={"status": "ACTIVE"},
    )
    assert wrong_status.status_code == 422


# --------------------------------------------------------- trace id 全链路一致
def test_trace_id_echoed_on_success_and_error(client: TestClient) -> None:
    resp = client.get("/v1/health", headers={"X-Trace-Id": "trace-smoke-123"})
    assert resp.headers.get("X-Trace-Id") == "trace-smoke-123"

    err = client.get(f"/v1/trips/{uuid.uuid4()}", headers=headers(ORG_A, USER_A))
    assert err.status_code == 404
    # 错误 body 内的 trace_id 必须与响应头一致（用了纯 ASGI 中间件才成立）。
    assert err.json()["error"]["trace_id"] == err.headers.get("X-Trace-Id")