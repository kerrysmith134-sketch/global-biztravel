"""客户多地址（PRD P0-3：一家企业有多个地址 + 信息来源及可信度）验收。"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.geo import Customer, CustomerPlace, Place
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


def make_place(client: TestClient, *, city: str, name: str) -> str:
    resp = client.post(
        "/v1/places",
        headers=headers(),
        json={
            "name": name,
            "city": city,
            "country_code": "DE",
            "address_line": f"{city} 主街 1 号",
            "lat": 52.52,
            "lon": 13.405,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_customer_multiple_addresses_with_source_and_confidence(client: TestClient) -> None:
    customer = client.post(
        "/v1/customers",
        headers=headers(),
        json={"name": f"多地址客户·{uuid.uuid4().hex[:8]}", "tier": "A"},
    )
    assert customer.status_code == 201, customer.text
    customer_id = customer.json()["id"]
    hq = make_place(client, city="Berlin", name="客户总部")
    plant = make_place(client, city="Munich", name="客户工厂")

    try:
        # 关联两个地址：总部为主，工厂带标签。
        for place_id, label, primary in ((hq, "总部", True), (plant, "工厂", False)):
            linked = client.post(
                f"/v1/customers/{customer_id}/places",
                headers=headers(),
                json={"place_id": place_id, "label": label, "is_primary": primary},
            )
            assert linked.status_code == 201, linked.text
            assert linked.json()["is_primary"] is primary

        # 幂等：重复关联返回同一组地址，不产生重复行。
        repeat = client.post(
            f"/v1/customers/{customer_id}/places",
            headers=headers(),
            json={"place_id": hq, "label": "总部", "is_primary": True},
        )
        assert repeat.status_code == 201, repeat.text

        listed = client.get(f"/v1/customers/{customer_id}/places", headers=headers())
        assert listed.status_code == 200, listed.text
        addresses = listed.json()
        assert len(addresses) == 2, addresses
        # 主地址排第一；来源与可信度随地址返回（PRD P0-3）。
        assert addresses[0]["place_id"] == hq
        assert addresses[0]["label"] == "总部"
        assert addresses[0]["geocode_source"] is not None
        assert addresses[0]["geocode_confidence"] is not None
        assert {a["label"] for a in addresses} == {"总部", "工厂"}

        # 解除关联：只删关联，不删地点。
        unlinked = client.delete(
            f"/v1/customers/{customer_id}/places/{plant}", headers=headers()
        )
        assert unlinked.status_code == 204, unlinked.text
        listed2 = client.get(f"/v1/customers/{customer_id}/places", headers=headers())
        assert [a["place_id"] for a in listed2.json()] == [hq]

        # 幂等删除：第二次 404。
        again = client.delete(
            f"/v1/customers/{customer_id}/places/{plant}", headers=headers()
        )
        assert again.status_code == 404

        with org_session(UUID(ORG_A)) as session:
            assert session.get(Place, UUID(plant)) is not None, "地点本体不应被删"
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(CustomerPlace).where(CustomerPlace.customer_id == UUID(customer_id))
            )
            session.execute(delete(Customer).where(Customer.id == UUID(customer_id)))
            session.execute(
                delete(Place).where(Place.id.in_([UUID(hq), UUID(plant)]))
            )


def test_customer_places_are_tenant_scoped(client: TestClient) -> None:
    customer = client.post(
        "/v1/customers",
        headers=headers(),
        json={"name": f"租户隔离客户·{uuid.uuid4().hex[:8]}"},
    )
    assert customer.status_code == 201, customer.text
    customer_id = customer.json()["id"]
    place_id = make_place(client, city="Hamburg", name="隔离测试地点")
    try:
        linked = client.post(
            f"/v1/customers/{customer_id}/places",
            headers=headers(),
            json={"place_id": place_id},
        )
        assert linked.status_code == 201

        # 跨租户：读列表 404（客户不存在于对方租户），关联动作同样 404。
        stolen_list = client.get(
            f"/v1/customers/{customer_id}/places", headers=headers(USER_B, ORG_B)
        )
        assert stolen_list.status_code == 404
        stolen_link = client.post(
            f"/v1/customers/{customer_id}/places",
            headers=headers(USER_B, ORG_B),
            json={"place_id": place_id},
        )
        assert stolen_link.status_code == 404

        # 关联不存在的地点 → 404，不产生脏关联。
        bad = client.post(
            f"/v1/customers/{customer_id}/places",
            headers=headers(),
            json={"place_id": str(uuid.uuid4())},
        )
        assert bad.status_code == 404
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(CustomerPlace).where(CustomerPlace.customer_id == UUID(customer_id))
            )
            session.execute(delete(Customer).where(Customer.id == UUID(customer_id)))
            session.execute(delete(Place).where(Place.id == UUID(place_id)))



def test_customer_map_locations_aggregate_filters_and_data_quality(client: TestClient) -> None:
    marker = f"mapgeo-{uuid.uuid4().hex[:10]}"
    customer_a = client.post(
        "/v1/customers",
        headers=headers(),
        json={"name": f"{marker} Customer A", "tier": "A", "country_code": "DE"},
    )
    customer_b = client.post(
        "/v1/customers",
        headers=headers(),
        json={"name": f"{marker} Customer B", "tier": "C", "country_code": "NL"},
    )
    customer_without_location = client.post(
        "/v1/customers",
        headers=headers(),
        json={"name": f"{marker} Customer Without Address", "tier": "B"},
    )
    assert customer_a.status_code == 201, customer_a.text
    assert customer_b.status_code == 201, customer_b.text
    assert customer_without_location.status_code == 201, customer_without_location.text
    customer_a_id = customer_a.json()["id"]
    customer_b_id = customer_b.json()["id"]
    customer_without_location_id = customer_without_location.json()["id"]

    resolved_place = make_place(client, city="Berlin", name=f"{marker} Berlin HQ")
    missing_place_response = client.post(
        "/v1/places",
        headers=headers(),
        json={"name": f"{marker} Unlocated Factory", "country_code": "NL"},
    )
    assert missing_place_response.status_code == 201, missing_place_response.text
    missing_place = missing_place_response.json()["id"]

    try:
        for customer_id, place_id, label, primary in (
            (customer_a_id, resolved_place, "HQ", True),
            (customer_a_id, missing_place, "Factory", False),
            (customer_b_id, resolved_place, "Partner office", True),
        ):
            response = client.post(
                f"/v1/customers/{customer_id}/places",
                headers=headers(),
                json={"place_id": place_id, "label": label, "is_primary": primary},
            )
            assert response.status_code == 201, response.text

        # Isolate test-owned customers from demo data already present in the shared CI tenant.
        result = client.get(
            "/v1/customers/map-locations",
            params={"limit": 200, "query": marker},
            headers=headers(),
        )
        assert result.status_code == 200, result.text
        body = result.json()
        assert len(body["items"]) == 3
        assert body["summary"]["locations_total"] == 3
        assert body["summary"]["locations_mapped"] == 2
        assert body["summary"]["locations_missing_coordinates"] == 1
        assert body["summary"]["customers_with_locations"] == 2
        # This quality counter deliberately remains organization-wide, outside the search filter.
        assert body["summary"]["customers_without_locations"] >= 1
        assert body["summary"]["countries"] == {"DE": 2, "NL": 1}

        filtered = client.get(
            "/v1/customers/map-locations",
            params={
                "query": marker,
                "tier": "A",
                "country_code": "DE",
                "geocode_status": "RESOLVED",
            },
            headers=headers(),
        )
        assert filtered.status_code == 200, filtered.text
        assert len(filtered.json()["items"]) == 1
        assert filtered.json()["items"][0]["customer"]["id"] == customer_a_id

        missing_only = client.get(
            "/v1/customers/map-locations",
            params={"query": marker, "geocode_status": "MISSING"},
            headers=headers(),
        )
        assert missing_only.status_code == 200, missing_only.text
        assert len(missing_only.json()["items"]) == 1
        assert missing_only.json()["items"][0]["place"]["id"] == missing_place

        # The aggregate is organization-scoped; another tenant sees neither customer nor place data.
        hidden = client.get(
            "/v1/customers/map-locations",
            params={"query": marker},
            headers=headers(USER_B, ORG_B),
        )
        assert hidden.status_code == 200, hidden.text
        assert hidden.json()["items"] == []
        assert hidden.json()["summary"]["locations_total"] == 0
    finally:
        with org_session(UUID(ORG_A)) as session:
            customer_ids = [
                UUID(customer_a_id),
                UUID(customer_b_id),
                UUID(customer_without_location_id),
            ]
            session.execute(delete(CustomerPlace).where(CustomerPlace.customer_id.in_(customer_ids)))
            session.execute(delete(Customer).where(Customer.id.in_(customer_ids)))
            session.execute(delete(Place).where(Place.id.in_([UUID(resolved_place), UUID(missing_place)])))
