"""H2.13 receipt asset storage and tenant-isolation acceptance tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.expenses import TripExpense
from app.models.receipts import ReceiptAsset
from app.models.trip import Trip
from app.services import receipt_asset_service
from app.services.receipt_storage_service import FilesystemReceiptStorage
from fastapi.testclient import TestClient
from sqlalchemy import delete

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
USER_A = "22222222-2222-2222-2222-222222222222"
USER_B = "44444444-4444-4444-4444-444444444444"

_PNG_1PX = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\x0bIDATx\x9cc\x00\x02\x00\x00\x05"
    b"\x00\x01\xa5\xf6E\x40\x00\x00\x00\x00IEND\xaeB\x60\x82"
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def private_storage(tmp_path, monkeypatch):
    storage = FilesystemReceiptStorage(tmp_path / "private-receipts")
    monkeypatch.setattr(receipt_asset_service, "get_receipt_storage", lambda provider=None: storage)
    return storage


def headers(org: str = ORG_A, user: str = USER_A, key: str | None = None) -> dict[str, str]:
    result = {"X-Org-Id": org, "X-User-Id": user}
    if key:
        result["Idempotency-Key"] = key
    return result


def create_trip(client: TestClient) -> str:
    now = datetime.now(UTC)
    response = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"票据测试·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=3)).isoformat(),
            "window_end": (now + timedelta(days=5)).isoformat(),
            "countries": ["DE"],
            "currency": "EUR",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_receipt_asset_upload_download_and_idempotency(
    client: TestClient, private_storage, monkeypatch
) -> None:
    trip_id = create_trip(client)
    key = str(uuid.uuid4())
    try:
        files = {"file": ("../unsafe/收据.png", _PNG_1PX, "image/png")}
        first = client.post(
            f"/v1/trips/{trip_id}/receipt-assets",
            headers=headers(key=key),
            files=files,
        )
        assert first.status_code == 201, first.text
        body = first.json()
        assert body["original_filename"] == "收据.png"
        assert body["mime_type"] == "image/png"
        assert body["byte_length"] == len(_PNG_1PX)
        assert body["storage_status"] == "AVAILABLE"
        assert "storage_key" not in body

        replay = client.post(
            f"/v1/trips/{trip_id}/receipt-assets",
            headers=headers(key=key),
            files=files,
        )
        assert replay.status_code == 201, replay.text
        assert replay.headers.get("Idempotent-Replay") == "true"
        assert replay.json()["id"] == body["id"]

        downloaded = client.get(
            f"/v1/receipt-assets/{body['id']}/content", headers=headers()
        )
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.content == _PNG_1PX
        assert downloaded.headers["cache-control"] == "private, no-store"
        assert downloaded.headers["x-content-type-options"] == "nosniff"
        assert "attachment" in downloaded.headers["content-disposition"]

        stolen = client.get(
            f"/v1/receipt-assets/{body['id']}/content",
            headers=headers(ORG_B, USER_B),
        )
        assert stolen.status_code == 404

        with org_session(UUID(ORG_A)) as session:
            asset = session.get(ReceiptAsset, UUID(body["id"]))
            assert asset is not None
            assert asset.storage_key.startswith("receipts/")
            assert asset.sha256
            receipt_id = asset.id
            storage_key = asset.storage_key
            session.execute(delete(ReceiptAsset).where(ReceiptAsset.id == receipt_id))
        assert private_storage.get(storage_key) == _PNG_1PX
        private_storage.delete(storage_key)
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))


def test_receipt_asset_rejects_mime_spoofing(client: TestClient, private_storage) -> None:
    trip_id = create_trip(client)
    try:
        response = client.post(
            f"/v1/trips/{trip_id}/receipt-assets",
            headers=headers(key=str(uuid.uuid4())),
            files={"file": ("receipt.png", b"not really a png", "image/png")},
        )
        assert response.status_code == 415, response.text
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))



def test_expense_can_only_reference_receipt_from_same_trip(
    client: TestClient, private_storage
) -> None:
    trip_id = create_trip(client)
    other_trip_id = create_trip(client)
    try:
        uploaded = client.post(
            f"/v1/trips/{trip_id}/receipt-assets",
            headers=headers(key=str(uuid.uuid4())),
            files={"file": ("receipt.png", _PNG_1PX, "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        asset_id = uploaded.json()["id"]
        expense_payload = {
            "category": "MEALS",
            "amount": "18.50",
            "currency": "EUR",
            "occurred_on": datetime.now(UTC).date().isoformat(),
            "receipt_asset_id": asset_id,
        }
        same_trip = client.post(
            f"/v1/trips/{trip_id}/expenses",
            headers=headers(key=str(uuid.uuid4())),
            json=expense_payload,
        )
        assert same_trip.status_code == 201, same_trip.text
        assert same_trip.json()["receipt_asset_id"] == asset_id

        cross_trip = client.post(
            f"/v1/trips/{other_trip_id}/expenses",
            headers=headers(key=str(uuid.uuid4())),
            json=expense_payload,
        )
        assert cross_trip.status_code == 422, cross_trip.text
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(TripExpense).where(
                    TripExpense.trip_id.in_([UUID(trip_id), UUID(other_trip_id)])
                )
            )
            session.execute(delete(Trip).where(Trip.id.in_([UUID(trip_id), UUID(other_trip_id)])))
