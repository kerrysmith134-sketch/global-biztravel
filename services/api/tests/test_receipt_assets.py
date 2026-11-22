"""H2.13 receipt asset storage and tenant-isolation acceptance tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.expense_reports import ExpenseReport
from app.models.expenses import TripExpense
from app.models.receipts import ReceiptAsset, ReceiptOcrReview, ReceiptOcrRun
from app.models.trip import Trip
from app.models.workflow import AgentRun
from app.services import receipt_asset_ocr_service, receipt_asset_service, receipt_ocr_service
from app.services.receipt_storage_service import FilesystemReceiptStorage
from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

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



def test_receipt_asset_download_rejects_changed_object_bytes(
    client: TestClient, private_storage
) -> None:
    trip_id = create_trip(client)
    try:
        uploaded = client.post(
            f"/v1/trips/{trip_id}/receipt-assets",
            headers=headers(key=str(uuid.uuid4())),
            files={"file": ("receipt.png", _PNG_1PX, "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        asset_id = uploaded.json()["id"]
        with org_session(UUID(ORG_A)) as session:
            asset = session.get(ReceiptAsset, UUID(asset_id))
            assert asset is not None
            storage_key = asset.storage_key
        private_storage.put(storage_key, b"tampered object bytes", "image/png")

        downloaded = client.get(
            f"/v1/receipt-assets/{asset_id}/content", headers=headers()
        )
        assert downloaded.status_code == 503, downloaded.text
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))



def test_expense_report_pins_receipt_identity_after_source_expense_is_deleted(
    client: TestClient, private_storage
) -> None:
    trip_id = create_trip(client)
    storage_key: str | None = None
    report_id: str | None = None
    asset_id: str | None = None
    try:
        uploaded = client.post(
            f"/v1/trips/{trip_id}/receipt-assets",
            headers=headers(key=str(uuid.uuid4())),
            files={"file": ("restaurant-receipt.png", _PNG_1PX, "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        asset = uploaded.json()
        asset_id = asset["id"]
        with org_session(UUID(ORG_A)) as session:
            stored_asset = session.get(ReceiptAsset, UUID(asset_id))
            assert stored_asset is not None
            storage_key = stored_asset.storage_key

        expense = client.post(
            f"/v1/trips/{trip_id}/expenses",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "category": "MEALS",
                "amount": "24.80",
                "currency": "EUR",
                "occurred_on": datetime.now(UTC).date().isoformat(),
                "receipt_asset_id": asset_id,
            },
        )
        assert expense.status_code == 201, expense.text
        expense_id = expense.json()["id"]

        created = client.post(
            f"/v1/trips/{trip_id}/expense-reports",
            headers=headers(key=str(uuid.uuid4())),
            json={},
        )
        assert created.status_code == 201, created.text
        report_id = created.json()["id"]
        item = created.json()["items"][0]
        assert item["receipt_asset_id"] == asset_id
        assert item["receipt_sha256"] == asset["sha256"]
        assert item["receipt_original_filename"] == "restaurant-receipt.png"
        assert item["receipt_mime_type"] == "image/png"
        assert item["receipt_byte_length"] == len(_PNG_1PX)

        removed = client.delete(f"/v1/expenses/{expense_id}", headers=headers())
        assert removed.status_code == 204
        detail = client.get(f"/v1/expense-reports/{report_id}", headers=headers())
        assert detail.status_code == 200, detail.text
        snap = detail.json()["items"][0]
        assert snap["expense_id"] is None
        assert snap["receipt_asset_id"] == asset_id
        assert snap["receipt_sha256"] == asset["sha256"]

        with pytest.raises(IntegrityError):
            with org_session(UUID(ORG_A)) as session:
                session.execute(
                    delete(ReceiptAsset).where(ReceiptAsset.id == UUID(asset_id))
                )

        original = client.get(
            f"/v1/receipt-assets/{asset_id}/content", headers=headers()
        )
        assert original.status_code == 200
        assert original.content == _PNG_1PX
    finally:
        with org_session(UUID(ORG_A)) as session:
            if report_id is not None:
                session.execute(
                    delete(ExpenseReport).where(ExpenseReport.id == UUID(report_id))
                )
            if asset_id is not None:
                session.execute(
                    delete(ReceiptAsset).where(ReceiptAsset.id == UUID(asset_id))
                )
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
        if storage_key is not None:
            private_storage.delete(storage_key)



def test_stored_receipt_ocr_agent_run_and_human_confirmation(
    client: TestClient, private_storage, monkeypatch
) -> None:
    trip_id = create_trip(client)
    storage_key: str | None = None
    agent_run_id: str | None = None
    try:
        uploaded = client.post(
            f"/v1/trips/{trip_id}/receipt-assets",
            headers=headers(key=str(uuid.uuid4())),
            files={"file": ("receipt.png", _PNG_1PX, "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        asset = uploaded.json()
        asset_id = asset["id"]
        with org_session(UUID(ORG_A)) as session:
            stored_asset = session.get(ReceiptAsset, UUID(asset_id))
            assert stored_asset is not None
            storage_key = stored_asset.storage_key

        seen: dict[str, object] = {}

        def fake_extract(
            image_bytes: bytes,
            *,
            file_extension: str,
            include_raw_text_preview: bool,
        ) -> dict:
            seen["bytes"] = image_bytes
            seen["extension"] = file_extension
            seen["include_raw_text_preview"] = include_raw_text_preview
            return {
                "merchant_hint": "Sample Restaurant",
                "amount_candidates": ["24.80"],
                "date_candidates": ["2026-11-23"],
                "text_length": 72,
                "raw_text_preview": "must not be persisted",
            }

        monkeypatch.setattr(receipt_ocr_service, "extract_receipt_fields", fake_extract)
        monkeypatch.setattr(
            receipt_ocr_service, "ocr_engine_version", lambda: "tesseract 5.3.4"
        )

        key = str(uuid.uuid4())
        extracted = client.post(
            f"/v1/receipt-assets/{asset_id}:ocr",
            headers=headers(key=key),
        )
        assert extracted.status_code == 201, extracted.text
        run = extracted.json()
        assert run["status"] == "SUCCEEDED"
        assert run["receipt_asset_id"] == asset_id
        assert run["content_sha256"] == asset["sha256"]
        assert run["engine"] == "tesseract"
        assert run["engine_version"] == "tesseract 5.3.4"
        assert run["candidates"]["merchant_hint"] == "Sample Restaurant"
        assert "raw_text_preview" not in run["candidates"]
        assert seen == {
            "bytes": _PNG_1PX,
            "extension": ".png",
            "include_raw_text_preview": False,
        }
        agent_run_id = run["agent_run_id"]

        replay = client.post(
            f"/v1/receipt-assets/{asset_id}:ocr",
            headers=headers(key=key),
        )
        assert replay.status_code == 201
        assert replay.headers.get("Idempotent-Replay") == "true"
        assert replay.json()["id"] == run["id"]

        detail = client.get(
            f"/v1/receipt-ocr-runs/{run['id']}", headers=headers()
        )
        assert detail.status_code == 200, detail.text
        assert detail.json()["review"] is None

        other_tenant = client.get(
            f"/v1/receipt-ocr-runs/{run['id']}",
            headers=headers(ORG_B, USER_B),
        )
        assert other_tenant.status_code == 404

        reviewed = client.post(
            f"/v1/receipt-ocr-runs/{run['id']}:review",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "decision": "CONFIRMED",
                "merchant": "Sample Restaurant Ltd.",
                "amount": "24.80",
                "currency": "eur",
                "occurred_on": "2026-11-23",
            },
        )
        assert reviewed.status_code == 201, reviewed.text
        review = reviewed.json()
        assert review["decision"] == "CONFIRMED"
        assert review["confirmed_fields"] == {
            "merchant": "Sample Restaurant Ltd.",
            "amount": "24.80",
            "currency": "EUR",
            "occurred_on": "2026-11-23",
        }
        assert review["reviewed_by_user_id"] == USER_A

        repeated_review = client.post(
            f"/v1/receipt-ocr-runs/{run['id']}:review",
            headers=headers(key=str(uuid.uuid4())),
            json={"decision": "REJECTED", "note": "Revised receipt required"},
        )
        assert repeated_review.status_code == 409

        # OCR review is evidence only; it must not create or alter a financial expense.
        expenses = client.get(f"/v1/trips/{trip_id}/expenses", headers=headers())
        assert expenses.status_code == 200
        assert expenses.json() == []

        asset_runs = client.get(
            f"/v1/receipt-assets/{asset_id}/ocr-runs", headers=headers()
        )
        assert asset_runs.status_code == 200
        assert len(asset_runs.json()) == 1
        assert asset_runs.json()[0]["review"]["id"] == review["id"]

        with org_session(UUID(ORG_A)) as session:
            stored_run = session.get(ReceiptOcrRun, UUID(run["id"]))
            stored_review = session.get(ReceiptOcrReview, UUID(review["id"]))
            agent_run = session.get(AgentRun, UUID(agent_run_id))
            assert stored_run is not None and stored_run.status == "SUCCEEDED"
            assert stored_review is not None
            assert agent_run is not None and agent_run.status == "SUCCEEDED"
            assert agent_run.input["content_sha256"] == asset["sha256"]
            assert "raw_text_preview" not in (agent_run.output or {})
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
            if agent_run_id is not None:
                session.execute(delete(AgentRun).where(AgentRun.id == UUID(agent_run_id)))
        if storage_key is not None:
            private_storage.delete(storage_key)


def test_stored_receipt_ocr_failure_is_persisted_without_false_success(
    client: TestClient, private_storage, monkeypatch
) -> None:
    trip_id = create_trip(client)
    storage_key: str | None = None
    agent_run_id: str | None = None
    try:
        uploaded = client.post(
            f"/v1/trips/{trip_id}/receipt-assets",
            headers=headers(key=str(uuid.uuid4())),
            files={"file": ("receipt.png", _PNG_1PX, "image/png")},
        )
        assert uploaded.status_code == 201, uploaded.text
        asset_id = uploaded.json()["id"]
        with org_session(UUID(ORG_A)) as session:
            stored_asset = session.get(ReceiptAsset, UUID(asset_id))
            assert stored_asset is not None
            storage_key = stored_asset.storage_key

        def fail_extract(*args, **kwargs):
            raise receipt_ocr_service.OcrUnavailable("tesseract is not installed")

        monkeypatch.setattr(receipt_ocr_service, "extract_receipt_fields", fail_extract)
        monkeypatch.setattr(
            receipt_ocr_service, "ocr_engine_version", lambda: "unavailable"
        )
        failed = client.post(
            f"/v1/receipt-assets/{asset_id}:ocr",
            headers=headers(key=str(uuid.uuid4())),
        )
        assert failed.status_code == 201, failed.text
        body = failed.json()
        assert body["status"] == "FAILED"
        assert body["error"] == "tesseract is not installed"
        agent_run_id = body["agent_run_id"]

        reviewed = client.post(
            f"/v1/receipt-ocr-runs/{body['id']}:review",
            headers=headers(key=str(uuid.uuid4())),
            json={
                "decision": "CONFIRMED",
                "amount": "24.80",
                "currency": "EUR",
            },
        )
        assert reviewed.status_code == 409
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
            if agent_run_id is not None:
                session.execute(delete(AgentRun).where(AgentRun.id == UUID(agent_run_id)))
        if storage_key is not None:
            private_storage.delete(storage_key)


def test_receipt_pdf_is_preserved_but_not_sent_to_image_ocr(
    client: TestClient, private_storage
) -> None:
    trip_id = create_trip(client)
    storage_key: str | None = None
    try:
        pdf_bytes = b"%PDF-1.4\n%receipt fixture\n%%EOF"
        uploaded = client.post(
            f"/v1/trips/{trip_id}/receipt-assets",
            headers=headers(key=str(uuid.uuid4())),
            files={"file": ("receipt.pdf", pdf_bytes, "application/pdf")},
        )
        assert uploaded.status_code == 201, uploaded.text
        asset_id = uploaded.json()["id"]
        with org_session(UUID(ORG_A)) as session:
            stored_asset = session.get(ReceiptAsset, UUID(asset_id))
            assert stored_asset is not None
            storage_key = stored_asset.storage_key

        result = client.post(
            f"/v1/receipt-assets/{asset_id}:ocr",
            headers=headers(key=str(uuid.uuid4())),
        )
        assert result.status_code == 415, result.text
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
        if storage_key is not None:
            private_storage.delete(storage_key)
