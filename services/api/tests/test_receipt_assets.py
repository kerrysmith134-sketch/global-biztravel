"""H2.13 receipt asset storage and tenant-isolation acceptance tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.receipts import ReceiptAsset
from app.models.trip import Trip
from app.services import receipt_storage_service
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
    monkeypatch.setattr(receipt_storage_service, "get_receipt_storage", lambda provider=None: storage)
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
            session.execute(delete(ReceiptAsset).where(ReceiptAsset.id == receipt_id))
        assert private_storage.get(
            f"receipts/{ORG_A}/{trip_id}/{body['id']}"
        ) == _PNG_1PX
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
