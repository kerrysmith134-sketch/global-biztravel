"""网页形式兜底（ADR-0034）：签证目录维护页 / 费用 CSV 导出 / 票据 OCR 候选。

- 签证：离线基线 + 组织覆盖合并；覆盖强制保留人工确认语义（ADR-0027）；
  维护页 ADMIN 专属、返回 HTML；
- 费用 CSV：列头固定、引号转义、公式前缀防注入；ADMIN 专属；
- OCR：纯提取函数单测（金额/日期/商户候选）；未装 Tesseract 端点 503 诚实失败。
"""

from __future__ import annotations

import csv
import io
import uuid
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import text

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(org: str = ORG_A, user: str = USER_A) -> dict[str, str]:
    return {"X-Org-Id": org, "X-User-Id": user}


# ---------------------------------------------------------------------------
# 签证目录覆盖：合并语义 + 人工确认强制
# ---------------------------------------------------------------------------


def test_visa_override_roundtrip_and_merge(client: TestClient):
    try:
        put = client.put(
            "/v1/admin/visa-catalog/xx",
            headers=headers(),
            json={
                "entry": {
                    "label": "测试国",
                    "business_entry": "商务访问（覆盖测试）",
                    "review_hint": "仅测试",
                }
            },
        )
        assert put.status_code == 200, put.text
        assert put.json()["entry"]["manual_confirmation_required"] is True

        merged = client.get("/v1/admin/visa-catalog", headers=headers())
        assert merged.status_code == 200
        countries = merged.json()["countries"]
        assert countries["XX"]["business_entry"] == "商务访问（覆盖测试）"
        # 基线条目仍在（合并不是替换整个目录）
        assert "DE" in countries
        # 覆盖条目的人工确认被强制为真——即使有人试图置假
        assert countries["XX"]["manual_confirmation_required"] is True

        remove = client.delete("/v1/admin/visa-catalog/xx", headers=headers())
        assert remove.status_code == 200
        after = client.get("/v1/admin/visa-catalog", headers=headers()).json()["countries"]
        assert "XX" not in after
        assert "DE" in after
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(text("DELETE FROM visa_catalog_overrides WHERE country_code = 'XX'"))


def test_visa_override_forces_manual_confirmation(client: TestClient):
    put = client.put(
        "/v1/admin/visa-catalog/DE",
        headers=headers(),
        json={
            "entry": {
                "label": "德国",
                "business_entry": "申根商务",
                "manual_confirmation_required": False,
            }
        },
    )
    assert put.status_code == 200
    assert put.json()["entry"]["manual_confirmation_required"] is True
    # 清理覆盖，回落基线
    client.delete("/v1/admin/visa-catalog/DE", headers=headers())


def test_visa_catalog_page_returns_html_for_admin(client: TestClient):
    page = client.get("/v1/admin/visa-catalog.html", headers=headers())
    assert page.status_code == 200
    assert page.headers["content-type"].startswith("text/html")
    assert "签证要求目录维护" in page.text

    traveler = {
        "X-Org-Id": "33333333-3333-3333-3333-333333333333",
        "X-User-Id": "44444444-4444-4444-4444-444444444444",
    }
    assert client.get("/v1/admin/visa-catalog.html", headers=traveler).status_code == 403


def test_visa_override_rejects_incomplete_entry(client: TestClient):
    response = client.put(
        "/v1/admin/visa-catalog/ZZ",
        headers=headers(),
        json={"entry": {"label": "只有名字"}},
    )
    assert response.status_code == 400, response.text


# ---------------------------------------------------------------------------
# 费用 CSV 导出：结构 + 防注入
# ---------------------------------------------------------------------------


def _expense_trip(client: TestClient) -> tuple[str, str]:
    place = client.post(
        "/v1/places",
        headers=headers(),
        json={"name": "CSV 导出客户", "lat": 48.13, "lon": 11.58, "city": "Munich"},
    )
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": "CSV 导出测试出行",
            "window_start": "2026-11-20T09:00:00+08:00",
            "window_end": "2026-11-22T18:00:00+08:00",
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    visit = client.post(
        f"/v1/trips/{trip.json()['id']}/visits",
        headers=headers(),
        json={"place_id": place.json()["id"], "title": "慕尼黑拜访", "service_minutes": 60},
    )
    assert visit.status_code == 201, visit.text
    return trip.json()["id"], place.json()["id"]


def _cleanup_expense_trip(client: TestClient, trip_id: str, place_id: str) -> None:
    client.delete(f"/v1/trips/{trip_id}/expenses/00000000-0000-0000-0000-000000000000", headers=headers())
    with org_session(UUID(ORG_A)) as session:
        session.execute(text("DELETE FROM trips WHERE id = :t"), {"t": trip_id})
        session.execute(text("DELETE FROM places WHERE id = :p"), {"p": place_id})


def test_expenses_csv_export(client: TestClient):
    trip_id, place_id = _expense_trip(client)
    try:
        created = client.post(
            f"/v1/trips/{trip_id}/expenses",
            headers={**headers(), "Idempotency-Key": str(uuid.uuid4())},
            json={
                "category": "MEALS",
                "amount": "42.50",
                "currency": "EUR",
                "occurred_on": "2026-11-20",
                "note": "=SUM(A1) 公式注入测试",
            },
        )
        assert created.status_code == 201, created.text

        export = client.get(f"/v1/trips/{trip_id}/expenses:export", headers=headers())
        assert export.status_code == 200
        assert export.headers["content-type"].startswith("text/csv")
        assert "attachment" in export.headers["content-disposition"]

        rows = list(csv.reader(io.StringIO(export.text)))
        assert rows[0] == ["occurred_on", "category", "amount", "currency", "note", "receipt_ref"]
        assert rows[1][1] == "MEALS"
        assert rows[1][2] == "42.50"
        # 公式前缀被加引号转义前缀，防 CSV 注入
        assert rows[1][4].startswith("'=")

        traveler = {
            "X-Org-Id": "33333333-3333-3333-3333-333333333333",
            "X-User-Id": "44444444-4444-4444-4444-444444444444",
        }
        assert client.get(f"/v1/trips/{trip_id}/expenses:export", headers=traveler).status_code == 403
    finally:
        _cleanup_expense_trip(client, trip_id, place_id)


# ---------------------------------------------------------------------------
# OCR：纯提取函数；未装二进制时端点 503
# ---------------------------------------------------------------------------

OCR_SAMPLE_TEXT = """星巴克咖啡
上海浦东机场店
2026-11-12 08:35
合计 ￥42.50
NO. 202611120001
"""


def test_ocr_amount_and_date_extraction():
    from app.services import receipt_ocr_service

    amounts = receipt_ocr_service._amounts(OCR_SAMPLE_TEXT)
    dates = receipt_ocr_service._dates(OCR_SAMPLE_TEXT)
    merchant = receipt_ocr_service._merchant_hint(OCR_SAMPLE_TEXT)

    assert any("42.50" in amount for amount in amounts)
    assert dates and "2026-11-12" in dates[0]
    assert merchant == "星巴克咖啡"


def test_ocr_route_honest_when_unavailable(client: TestClient):
    from app.services import receipt_ocr_service

    if receipt_ocr_service.ocr_available():
        pytest.skip("本机已装 tesseract，走成功路径由人工冒烟覆盖")
    response = client.post(
        "/v1/expenses:ocr",
        headers=headers(),
        files={"file": ("receipt.png", b"\x89PNG fake", "image/png")},
    )
    assert response.status_code == 503
    assert "brew install" in response.text
