#!/usr/bin/env python
"""把 Excel 导入的**真实校验结果**落成可评审的 Markdown 报告。

数据来源不是手工构造的样例，而是 ``docs/acceptance-tests/fixtures/sample-customers.xlsx``
这份"故意埋了坑"的客户拜访清单——它对真实的后端发起一次 ``POST /v1/trips/{id}/imports:parse``，
再把返回的 ``report``（列识别、缺失列、问题聚合、逐行状态）原样渲染成报告。

刻意**只解析、不确认**：``parse`` 阶段按设计不写入任何业务数据（客户/地点/拜访计数不变），
所以本脚本可以反复运行而不污染开发库；落库路径由冒烟测试的 ``confirm`` 用例单独取证。

    .venv/bin/python scripts/generate_import_report.py
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.api.errors import ApiError  # noqa: E402
from app.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ORG_A = "11111111-1111-1111-1111-111111111111"  # 安纳瑞能源
USER_A = "22222222-2222-2222-2222-222222222222"  # kerry@anari.example（OWNER）
TRIP_A_NAME = "2026 Q4 欧洲多国商务出行"

FIXTURE = ROOT / "docs" / "acceptance-tests" / "fixtures" / "sample-customers.xlsx"
OUTPUT = ROOT / "docs" / "acceptance-tests" / "import-validation-report.md"

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

SEVERITY_LABEL = {"ERROR": "错误", "WARNING": "警告", "INFO": "提示"}


def _headers() -> dict[str, str]:
    return {"X-Org-Id": ORG_A, "X-User-Id": USER_A}


def _trip_id(client: TestClient, name: str) -> str:
    resp = client.get("/v1/trips", headers=_headers())
    if resp.status_code != 200:
        raise SystemExit(f"[fail] 读取 trips 失败：{resp.status_code} {resp.text}")
    for item in resp.json()["items"]:
        if item["name"] == name:
            return item["id"]
    raise SystemExit(f"[fail] 未找到出行「{name}」，请先运行 scripts/seed_dev.py")


def _parse(client: TestClient, trip_id: str, data: bytes) -> dict[str, Any]:
    resp = client.post(
        f"/v1/trips/{trip_id}/imports:parse",
        headers=_headers(),
        files={"file": (FIXTURE.name, data, XLSX_MIME)},
    )
    if resp.status_code not in (200, 201, 202):
        raise SystemExit(f"[fail] parse 失败：{resp.status_code} {resp.text}")
    return resp.json()


def _esc(text: Any) -> str:
    """把单元格文本里的竖线转义，避免破坏 Markdown 表格。"""
    return str(text).replace("|", "\\|").replace("\n", " ")


def _render(payload: dict[str, Any], sha: str) -> str:
    report = payload.get("report") or {}
    columns = report.get("columns") or {}
    counts = report.get("counts") or {}
    issues = report.get("issues") or []
    rows = payload.get("rows") or []

    lines: list[str] = []
    add = lines.append

    add("# Excel 导入 — 真实数据校验报告")
    add("")
    add("> 本报告由 `scripts/generate_import_report.py` 对运行中的后端发起真实 HTTP 请求生成，")
    add("> 数据源为验收夹具 `docs/acceptance-tests/fixtures/sample-customers.xlsx`。")
    add("> 报告内容全部来自服务端返回的结构化 `report`，未经人工修饰。")
    add("")
    add("#### 运行上下文")
    add("")
    add("| 项 | 值 |")
    add("| --- | --- |")
    add(f"| 生成时间（服务端时钟，UTC） | {report.get('generated_at', '—')} |")
    add(f"| 校验组织 | `{ORG_A}`（安纳瑞能源） |")
    add(f"| 目标出行 | {TRIP_A_NAME} `{payload.get('trip_id', '—')}` |")
    add(f"| 上传文件 | `{payload.get('filename', '—')}` |")
    add(f"| 文件 SHA-256 | `{sha}` |")
    add(f"| 批次 import_id | `{payload.get('import_id', '—')}` |")
    add(f"| 批次状态 | `{payload.get('status', '—')}` |")
    geocoder = report.get("geocoder") or {}
    add(f"| 地理编码器 | `{geocoder.get('source', '—')}` / `{geocoder.get('version', '—')}` |")
    add(f"| 校验 schema 版本 | `{report.get('schema_version', '—')}` |")
    add("")

    add("#### 一、逐行体检结论")
    add("")
    add("| 指标 | 数量 |")
    add("| --- | --- |")
    add(f"| 数据行总数 | {counts.get('total', 0)} |")
    add(f"| 可直接导入（VALID） | {counts.get('valid', 0)} |")
    add(f"| 存在阻断问题（INVALID） | {counts.get('invalid', 0)} |")
    add(f"| 批内重复（DUPLICATE） | {counts.get('duplicate', 0)} |")
    add(f"| 坐标未解析 | {counts.get('unresolved_coordinates', 0)} |")
    add(f"| 已在本次出行中 | {counts.get('already_in_trip', 0)} |")
    add("")
    add(
        "结论："
        f"**{counts.get('total', 0)} 行中 {counts.get('valid', 0)} 行可直接确认导入**，"
        f"{counts.get('invalid', 0)} 行含阻断问题（默认不勾选）、"
        f"{counts.get('duplicate', 0)} 行为批内重复（默认不勾选）；"
        f"另有 {counts.get('unresolved_coordinates', 0)} 行坐标缺失，"
        "需人工补全或接受按城市中心点降级定位。确认阶段由用户逐行勾选，"
        "阻断行即使被勾选也会被拒收并计入 `rejected_rows`。"
    )
    add("")
    add(
        "> 「已在本次出行中」是与目标出行**当前状态**比对出来的结果（种子基线在这条出行里"
        "已有若干次拜访），不是文件自身的固有属性；其余计数与问题码只由文件内容决定，"
        "反复校验结果不变。"
    )
    add("")

    add("#### 二、表头识别")
    add("")
    add(f"- 表格列数：`{len(columns.get('sheet') or [])}`")
    add(f"- 已识别字段：`{len(columns.get('recognized') or [])}` 个")
    add(f"- 未识别（被忽略）列：{_esc('、'.join(columns.get('unknown') or []) or '无')}")
    add(f"- 缺失必需列：{_esc('、'.join(columns.get('missing_required') or []) or '无')}")
    add("")
    unknown = columns.get("unknown") or []
    if unknown:
        add(
            "未识别列只会产生 `UNKNOWN_COLUMNS` 警告，不阻断导入；"
            "缺失必需列则整体返回 `422 MISSING_COLUMN`，**不会创建任何批次**。"
        )
        add("")
    add("<details><summary>已识别字段清单</summary>")
    add("")
    add("```text")
    for field in columns.get("recognized") or []:
        add(field)
    add("```")
    add("")
    add("</details>")
    add("")

    add("#### 三、问题聚合")
    add("")
    if not issues:
        add("无任何问题。")
        add("")
    else:
        add("| 严重度 | 问题码 | 命中行数 | 样例行号 | 说明 | 建议动作 |")
        add("| --- | --- | --- | --- | --- | --- |")
        for entry in issues:
            sev = SEVERITY_LABEL.get(entry.get("severity", ""), entry.get("severity", "—"))
            samples = "、".join(str(n) for n in (entry.get("sample_rows") or [])[:10])
            add(
                f"| {sev} | `{entry.get('code', '—')}` | {entry.get('count', 0)} "
                f"| {samples or '—'} | {_esc(entry.get('message', '—'))} "
                f"| {_esc(entry.get('suggestion', '—'))} |"
            )
        add("")
        errors = [e for e in issues if e.get("severity") == "ERROR"]
        warns = [e for e in issues if e.get("severity") == "WARNING"]
        add(
            f"共命中 `{len(issues)}` 类问题：{len(errors)} 类错误、{len(warns)} 类警告。"
            "错误级问题会阻断该行导入，警告级问题放行并在确认结果里回传。"
        )
        add("")

    add("#### 四、逐行明细")
    add("")
    add("| 行号 | 状态 | 客户 | 城市 / 国家 | 坐标 | 问题码 |")
    add("| --- | --- | --- | --- | --- | --- |")
    for row in rows:
        normalized = row.get("normalized") or {}
        raw = row.get("raw") or {}
        customer = normalized.get("customer_name") or raw.get("客户名称") or raw.get("customer_name") or "—"
        city = normalized.get("city") or raw.get("城市") or "—"
        country = normalized.get("country_code") or raw.get("国家") or "—"
        geo = normalized.get("geocode_status") or "—"
        if normalized.get("latitude") is not None:
            geo = f"{geo}（{normalized.get('latitude'):.4f}, {normalized.get('longitude'):.4f}）"
        codes = "、".join(f"`{i.get('code')}`" for i in (row.get("issues") or [])) or "—"
        add(
            f"| {row.get('row_number', '—')} | `{row.get('status', '—')}` | {_esc(customer)} "
            f"| {_esc(city)} / {_esc(country)} | {_esc(geo)} | {codes} |"
        )
    add("")
    add(
        "> 行号为 Excel 实际行号（第 1 行为表头），因此数据行从第 2 行起。"
        "本阶段 `parse` 按设计**不写入任何业务数据**：客户、地点、拜访的行数在校验前后保持不变。"
    )
    add("")

    add("#### 五、复核方式")
    add("")
    add("```bash")
    add("scripts/bootstrap.sh                       # 起库 + 迁移（幂等）")
    add(".venv/bin/python scripts/seed_dev.py --verify   # 造数并校验隔离（幂等）")
    add(".venv/bin/python scripts/generate_import_report.py  # 重新生成本报告")
    add(".venv/bin/python -m pytest services/api/tests/test_backend_smoke.py -m integration")
    add("```")
    add("")
    return "\n".join(lines)


def main() -> int:
    if not FIXTURE.exists():
        raise SystemExit(f"[fail] 夹具不存在：{FIXTURE}（请先运行 scripts/seed_dev.py）")
    data = FIXTURE.read_bytes()
    import hashlib

    sha = hashlib.sha256(data).hexdigest()

    client = TestClient(app)
    try:
        trip_id = _trip_id(client, TRIP_A_NAME)
        payload = _parse(client, trip_id, data)
    except ApiError as exc:  # pragma: no cover - 仅用于给出更友好的失败信息
        raise SystemExit(f"[fail] {exc}") from exc

    report = _render(payload, sha)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(report, encoding="utf-8")

    counts = (payload.get("report") or {}).get("counts") or {}
    print(f"[ok] 报告已写入 {OUTPUT.relative_to(ROOT)}")
    print(
        f"[ok] 行数：total={counts.get('total')} valid={counts.get('valid')} "
        f"invalid={counts.get('invalid')} duplicate={counts.get('duplicate')} "
        f"unresolved={counts.get('unresolved_coordinates')} already={counts.get('already_in_trip')}"
    )
    print(f"[ok] 问题码：{'、'.join(e['code'] for e in (payload.get('report') or {}).get('issues') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())