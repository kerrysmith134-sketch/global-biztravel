"""入境可行性结合证件/国籍（PRD P0-7）验收。

PRD：「结合用户实际证件、国籍、签证和过境情况检查可行性」「对信息不足的情况主动提示」。
边界：目录只按国别组织、不含按国籍的免签/需签结论——评估只陈述"以哪本护照为前提"
与缺口，不宣布确定性入境结论（方向二红线）。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.trip import Trip
from fastapi.testclient import TestClient
from sqlalchemy import delete

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
ADMIN_A = "55555555-5555-5555-5555-555555555555"
USER_B = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(user: str = ADMIN_A, org: str = ORG_A) -> dict[str, str]:
    return {
        "X-Org-Id": org,
        "X-User-Id": user,
        "Idempotency-Key": str(uuid.uuid4()),
    }


def make_trip(client: TestClient) -> str:
    now = datetime.now(UTC)
    resp = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"证件核验·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=20)).isoformat(),
            "window_end": (now + timedelta(days=27)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_profile_nationality_roundtrip_and_validation(client: TestClient) -> None:
    try:
        # 未登记：读到 null（不猜默认国籍）。
        empty = client.get("/v1/traveler/profile", headers=headers())
        assert empty.status_code == 200
        assert empty.json().get("nationality") in (None, "")

        # 登记国籍（ISO alpha-2 大写）。
        set_resp = client.put(
            "/v1/traveler/profile",
            headers=headers(),
            json={"nationality": "cn"},
        )
        assert set_resp.status_code == 200, set_resp.text
        assert set_resp.json()["nationality"] == "CN"

        # 幂等：内容没变不升版本。
        again = client.put(
            "/v1/traveler/profile", headers=headers(), json={"nationality": "CN"}
        )
        assert again.status_code == 200
        assert again.json()["version"] == set_resp.json()["version"]

        # 非法国籍被拒（不是两位大写字母）。
        bad = client.put(
            "/v1/traveler/profile", headers=headers(), json={"nationality": "China"}
        )
        assert bad.status_code in (400, 422)

        # 显式清除（nationality 字段出现且为 null）。
        cleared = client.put(
            "/v1/traveler/profile", headers=headers(), json={"nationality": None}
        )
        assert cleared.status_code == 200
        assert cleared.json()["nationality"] in (None, "")

        # 只改 notes 不动国籍：清空后保持清空。
        notes_only = client.put(
            "/v1/traveler/profile", headers=headers(), json={"notes": "常飞欧洲"}
        )
        assert notes_only.status_code == 200
        assert notes_only.json()["nationality"] in (None, "")
    finally:
        # 还原为空画像，避免污染其它用例。
        client.put(
            "/v1/traveler/profile", headers=headers(), json={"nationality": None}
        )


def test_assessment_states_credential_premise(client: TestClient) -> None:
    trip_id = make_trip(client)
    try:
        # 无国籍：评估必须显式指出无法结合证件核验，且置信度更低。
        client.put("/v1/traveler/profile", headers=headers(), json={"nationality": None})
        without = client.post(
            f"/v1/trips/{trip_id}/entry-assessments:refresh", headers=headers()
        )
        assert without.status_code == 200, without.text
        no_nat = without.json()[0]
        assert "未登记护照国籍" in no_nat["summary"], no_nat["summary"]
        assert no_nat["manual_review_required"] is True
        assert no_nat["confidence"] <= 0.35

        # 登记国籍后：陈述"以哪本护照为前提"，但**不**宣布需要/免签结论。
        client.put(
            "/v1/traveler/profile", headers=headers(), json={"nationality": "CN"}
        )
        with_nat = client.post(
            f"/v1/trips/{trip_id}/entry-assessments:refresh", headers=headers()
        )
        assert with_nat.status_code == 200, with_nat.text
        yes = with_nat.json()[0]
        assert "CN 护照" in yes["summary"], yes["summary"]
        assert "证件前提" in yes["summary"]
        # 红线：不出现确定性入境结论。
        for banned in ("免签", "需要签证", "无需签证", "落地签可"):
            assert banned not in yes["summary"], yes["summary"]
        assert yes["manual_review_required"] is True
        assert yes["confidence"] >= no_nat["confidence"]
    finally:
        client.put(
            "/v1/traveler/profile", headers=headers(), json={"nationality": None}
        )
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))


def test_nationality_is_tenant_scoped(client: TestClient) -> None:
    try:
        client.put(
            "/v1/traveler/profile", headers=headers(), json={"nationality": "CN"}
        )
        # 另一租户看不到本租户的画像。
        stolen = client.get(
            "/v1/traveler/profile", headers=headers(USER_B, ORG_B)
        )
        assert stolen.status_code == 200
        assert stolen.json().get("nationality") in (None, "")

        with org_session(UUID(ORG_A)) as session:
            trip = session.get(Trip, uuid.UUID(int=0))  # 占位，不使用
            assert trip is None
    finally:
        client.put(
            "/v1/traveler/profile", headers=headers(), json={"nationality": None}
        )
