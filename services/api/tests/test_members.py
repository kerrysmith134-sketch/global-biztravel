"""团队协作：成员列表/添加/角色变更/移除（PRD P1）验收。"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from app.db import owner_session
from app.main import app
from app.models.identity import Membership, User
from fastapi.testclient import TestClient
from sqlalchemy import delete

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
OWNER_A = "22222222-2222-2222-2222-222222222222"  # kerry（OWNER）
ADMIN_A = "55555555-5555-5555-5555-555555555555"  # mia（ADMIN）
USER_B = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(user: str = OWNER_A, org: str = ORG_A) -> dict[str, str]:
    return {
        "X-Org-Id": org,
        "X-User-Id": user,
        "Idempotency-Key": str(uuid.uuid4()),
    }


def _cleanup_users(user_ids: list[UUID]) -> None:
    with owner_session() as session:
        session.execute(delete(Membership).where(Membership.user_id.in_(user_ids)))
        session.execute(delete(User).where(User.id.in_(user_ids)))


def test_list_members_and_add_role_change_remove(client: TestClient) -> None:
    temp_user = uuid.uuid4()
    email = f"member-{uuid.uuid4().hex[:8]}@anari.example"
    try:
        with owner_session() as session:
            session.add(User(id=temp_user, email=email, display_name="临时成员"))
            session.flush()
            session.add(
                Membership(organization_id=UUID(ORG_A), user_id=temp_user, role="TRAVELER")
            )

        # 列表：OWNER 可见所有成员及角色。
        listed = client.get("/v1/members", headers=headers())
        assert listed.status_code == 200, listed.text
        roles = {m["user_id"]: m["role"] for m in listed.json()}
        assert roles[str(OWNER_A)] == "OWNER"
        assert roles[str(ADMIN_A)] == "ADMIN"
        assert roles[str(temp_user)] == "TRAVELER"

        # ADMIN 变更角色。
        promoted = client.patch(
            f"/v1/members/{temp_user}", headers=headers(), json={"role": "ADMIN"}
        )
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["role"] == "ADMIN"

        # ADMIN 不能把某成员升为 OWNER。
        owner_attempt = client.patch(
            f"/v1/members/{temp_user}", headers=headers(ADMIN_A), json={"role": "OWNER"}
        )
        assert owner_attempt.status_code in (400, 422)

        # OWNER 降级自己（最后一位）→ 拒绝。
        demote = client.patch(
            f"/v1/members/{OWNER_A}", headers=headers(), json={"role": "TRAVELER"}
        )
        assert demote.status_code in (400, 422)

        # 移除临时成员。
        removed = client.delete(f"/v1/members/{temp_user}", headers=headers())
        assert removed.status_code == 204, removed.text

        # 跨租户看不到成员列表。
        stolen = client.get("/v1/members", headers=headers(USER_B, ORG_B))
        assert stolen.status_code == 200
        assert not any(m["user_id"] == str(OWNER_A) for m in stolen.json())
    finally:
        _cleanup_users([temp_user])


def test_insufficient_role_rejected(client: TestClient) -> None:
    # TRAVELER 或非本组织用户不能管理成员。
    stranger = uuid.uuid4()
    try:
        with owner_session() as session:
            session.add(
                User(id=stranger, email=f"x-{uuid.uuid4().hex[:8]}@anari.example", display_name="X")
            )
        denied = client.get(
            "/v1/members", headers=headers(str(stranger), ORG_A)
        )
        # 没有本组织 membership → RLS 或权限拒绝（403/404 都可）。
        assert denied.status_code in (403, 404)

        cross_org_add = client.post(
            "/v1/members",
            headers=headers(USER_B, ORG_B),
            json={"user_id": str(uuid.uuid4()), "role": "TRAVELER"},
        )
        assert cross_org_add.status_code in (403, 404)
    finally:
        _cleanup_users([stranger])
