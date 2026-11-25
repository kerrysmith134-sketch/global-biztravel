"""H2.14 receipt storage orphan reconciliation safety tests."""

from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.receipts import ReceiptAsset, ReceiptStorageOrphan
from app.models.trip import Trip
from app.services import receipt_storage_reconciliation_service
from app.services.receipt_storage_service import (
    FilesystemReceiptStorage,
    ReceiptStorageUnavailable,
)
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
USER_A = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def private_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FilesystemReceiptStorage:
    storage = FilesystemReceiptStorage(tmp_path / "private-receipts")
    monkeypatch.setattr(
        receipt_storage_reconciliation_service,
        "get_receipt_storage",
        lambda provider=None: storage,
    )
    return storage


def headers(key: str | None = None) -> dict[str, str]:
    result = {"X-Org-Id": ORG_A, "X-User-Id": USER_A}
    if key:
        result["Idempotency-Key"] = key
    return result


def create_trip(client: TestClient) -> str:
    now = datetime.now(UTC)
    response = client.post(
        "/v1/trips",
        headers=headers(str(uuid.uuid4())),
        json={
            "name": f"票据对账测试·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(days=3)).isoformat(),
            "window_end": (now + timedelta(days=5)).isoformat(),
            "countries": ["DE"],
            "currency": "EUR",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def make_storage_key(trip_id: str | None = None) -> str:
    return f"receipts/{ORG_A}/{trip_id or uuid.uuid4()}/{uuid.uuid4()}"


def put_orphan(storage: FilesystemReceiptStorage, key: str, *, modified_at: datetime) -> bytes:
    content = b"private receipt orphan candidate"
    storage.put(key, content, "application/octet-stream")
    path = storage.root / key
    os.utime(path, (modified_at.timestamp(), modified_at.timestamp()))
    return content


def reconcile(
    *,
    now: datetime,
    cleanup_enabled: bool,
    grace_seconds: int = 60,
) -> receipt_storage_reconciliation_service.ReceiptReconciliationReport:
    with org_session(UUID(ORG_A)) as session:
        return receipt_storage_reconciliation_service.reconcile_receipt_storage(
            session,
            ORG_A,
            now=now,
            cleanup_enabled=cleanup_enabled,
            grace_seconds=grace_seconds,
        )


def orphan_state(key: str) -> ReceiptStorageOrphan:
    with org_session(UUID(ORG_A)) as session:
        row = session.scalars(
            select(ReceiptStorageOrphan).where(
                ReceiptStorageOrphan.organization_id == ORG_A,
                ReceiptStorageOrphan.storage_key == key,
            )
        ).one()
        # Return a detached scalar snapshot; the Session closes after this function.
        session.expunge(row)
        return row


def test_orphan_is_discovered_before_it_can_be_deleted(
    private_storage: FilesystemReceiptStorage,
) -> None:
    now = datetime.now(UTC)
    key = make_storage_key()
    original = put_orphan(
        private_storage,
        key,
        modified_at=now - timedelta(days=3),
    )

    first = reconcile(now=now, cleanup_enabled=True, grace_seconds=60)
    assert first.objects_scanned == 1
    assert first.orphans_discovered == 1
    assert first.orphans_eligible == 0
    assert first.objects_deleted == 0
    assert private_storage.get(key) == original

    second = reconcile(now=now + timedelta(seconds=61), cleanup_enabled=True, grace_seconds=60)
    assert second.orphans_eligible == 1
    assert second.objects_deleted == 1
    assert second.delete_failures == 0
    assert orphan_state(key).status == "DELETED"
    assert orphan_state(key).delete_attempts == 1
    with pytest.raises(ReceiptStorageUnavailable):
        private_storage.get(key)


def test_cleanup_disabled_is_a_durable_dry_run(
    private_storage: FilesystemReceiptStorage,
) -> None:
    now = datetime.now(UTC)
    key = make_storage_key()
    original = put_orphan(
        private_storage,
        key,
        modified_at=now - timedelta(days=3),
    )

    reconcile(now=now - timedelta(seconds=120), cleanup_enabled=False, grace_seconds=60)
    report = reconcile(now=now, cleanup_enabled=False, grace_seconds=60)

    assert report.cleanup_enabled is False
    assert report.orphans_eligible == 1
    assert report.objects_deleted == 0
    assert orphan_state(key).status == "DISCOVERED"
    assert private_storage.get(key) == original


def test_recent_object_is_not_deleted_even_when_observation_is_old(
    private_storage: FilesystemReceiptStorage,
) -> None:
    now = datetime.now(UTC)
    key = make_storage_key()
    original = put_orphan(private_storage, key, modified_at=now)

    # Simulate an object that was first observed before its object timestamp existed.
    reconcile(now=now - timedelta(days=2), cleanup_enabled=True, grace_seconds=60)
    report = reconcile(now=now, cleanup_enabled=True, grace_seconds=60)

    assert report.orphans_eligible == 0
    assert report.objects_deleted == 0
    assert orphan_state(key).status == "DISCOVERED"
    assert private_storage.get(key) == original


def test_object_becomes_resolved_when_receipt_metadata_is_committed(
    client: TestClient,
    private_storage: FilesystemReceiptStorage,
) -> None:
    trip_id = create_trip(client)
    now = datetime.now(UTC)
    asset_id = uuid.uuid4()
    key = f"receipts/{ORG_A}/{trip_id}/{asset_id}"
    content = put_orphan(
        private_storage,
        key,
        modified_at=now - timedelta(days=3),
    )
    try:
        discovered = reconcile(now=now, cleanup_enabled=True, grace_seconds=60)
        assert discovered.orphans_discovered == 1
        assert orphan_state(key).status == "DISCOVERED"

        with org_session(UUID(ORG_A)) as session:
            session.add(
                ReceiptAsset(
                    id=asset_id,
                    organization_id=UUID(ORG_A),
                    trip_id=UUID(trip_id),
                    storage_provider="filesystem",
                    storage_key=key,
                    original_filename="receipt.png",
                    mime_type="image/png",
                    byte_length=len(content),
                    sha256=hashlib.sha256(content).hexdigest(),
                    storage_status="AVAILABLE",
                    uploaded_at=now,
                )
            )

        resolved = reconcile(
            now=now + timedelta(hours=2),
            cleanup_enabled=True,
            grace_seconds=60,
        )
        assert resolved.resolved == 1
        assert resolved.objects_deleted == 0
        assert orphan_state(key).status == "RESOLVED"
        assert private_storage.get(key) == content
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))


def test_delete_failure_is_audited_and_retried(
    private_storage: FilesystemReceiptStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(UTC)
    key = make_storage_key()
    original = put_orphan(
        private_storage,
        key,
        modified_at=now - timedelta(days=3),
    )
    reconcile(now=now, cleanup_enabled=True, grace_seconds=60)

    original_delete = private_storage.delete

    def fail_once(object_key: str) -> None:
        raise ReceiptStorageUnavailable("simulated object-store outage")

    monkeypatch.setattr(private_storage, "delete", fail_once)
    failed = reconcile(
        now=now + timedelta(seconds=61),
        cleanup_enabled=True,
        grace_seconds=60,
    )
    assert failed.delete_failures == 1
    assert orphan_state(key).status == "DELETE_FAILED"
    assert orphan_state(key).delete_attempts == 1
    assert private_storage.get(key) == original

    monkeypatch.setattr(private_storage, "delete", original_delete)
    retried = reconcile(
        now=now + timedelta(seconds=122),
        cleanup_enabled=True,
        grace_seconds=60,
    )
    assert retried.objects_deleted == 1
    assert orphan_state(key).status == "DELETED"
    assert orphan_state(key).delete_attempts == 2



def test_database_backed_asset_missing_from_listing_is_checked_with_stat(
    client: TestClient,
    private_storage: FilesystemReceiptStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trip_id = create_trip(client)
    now = datetime.now(UTC)
    asset_id = uuid.uuid4()
    key = f"receipts/{ORG_A}/{trip_id}/{asset_id}"
    content = b"valid object that should not be diagnosed as missing"
    private_storage.put(key, content, "application/pdf")
    with org_session(UUID(ORG_A)) as session:
        session.add(
            ReceiptAsset(
                id=asset_id,
                organization_id=UUID(ORG_A),
                trip_id=UUID(trip_id),
                storage_provider="filesystem",
                storage_key=key,
                original_filename="receipt.pdf",
                mime_type="application/pdf",
                byte_length=len(content),
                sha256="a" * 64,
                storage_status="AVAILABLE",
                uploaded_at=now,
            )
        )

    # Simulate a listing snapshot that missed an object committed immediately before
    # reconciliation acquired the tenant lock. Exact-key stat must recover it.
    monkeypatch.setattr(private_storage, "list_objects", lambda prefix: [])
    try:
        report = reconcile(now=now, cleanup_enabled=True, grace_seconds=1)
        assert report.objects_scanned == 1
        assert report.missing == 0
        with org_session(UUID(ORG_A)) as session:
            incidents = session.scalars(
                select(ReceiptStorageOrphan).where(
                    ReceiptStorageOrphan.organization_id == ORG_A,
                    ReceiptStorageOrphan.storage_key == key,
                )
            ).all()
        assert incidents == []
        assert private_storage.stat_object(key) is not None
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))


def test_missing_object_for_existing_asset_is_audited_not_deleted(
    client: TestClient,
    private_storage: FilesystemReceiptStorage,
) -> None:
    trip_id = create_trip(client)
    now = datetime.now(UTC)
    asset_id = uuid.uuid4()
    key = f"receipts/{ORG_A}/{trip_id}/{asset_id}"
    content = b"receipt object that disappears"
    private_storage.put(key, content, "application/pdf")
    with org_session(UUID(ORG_A)) as session:
        session.add(
            ReceiptAsset(
                id=asset_id,
                organization_id=UUID(ORG_A),
                trip_id=UUID(trip_id),
                storage_provider="filesystem",
                storage_key=key,
                original_filename="missing.pdf",
                mime_type="application/pdf",
                byte_length=len(content),
                sha256="b" * 64,
                storage_status="AVAILABLE",
                uploaded_at=now,
            )
        )
    private_storage.delete(key)
    try:
        report = reconcile(now=now, cleanup_enabled=True, grace_seconds=1)
        assert report.missing == 1
        assert report.objects_deleted == 0
        with org_session(UUID(ORG_A)) as session:
            incident = session.scalars(
                select(ReceiptStorageOrphan).where(
                    ReceiptStorageOrphan.organization_id == ORG_A,
                    ReceiptStorageOrphan.storage_key == key,
                )
            ).one()
            assert incident.status == "MISSING"
            asset = session.get(ReceiptAsset, asset_id)
            assert asset is not None
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))


def test_filesystem_listing_cannot_escape_requested_tenant_prefix(
    private_storage: FilesystemReceiptStorage,
) -> None:
    key_a = make_storage_key()
    key_b = f"receipts/33333333-3333-4333-8333-333333333333/{uuid.uuid4()}/{uuid.uuid4()}"
    modified = datetime.now(UTC) - timedelta(days=2)
    put_orphan(private_storage, key_a, modified_at=modified)
    put_orphan(private_storage, key_b, modified_at=modified)

    listed = private_storage.list_objects(f"receipts/{ORG_A}/")
    assert [item.key for item in listed] == [key_a]
    with pytest.raises(ValueError):
        private_storage.list_objects("receipts/")


def test_filesystem_listing_ignores_symlinks_and_delete_refuses_them(
    private_storage: FilesystemReceiptStorage,
) -> None:
    real_key = make_storage_key()
    alias_key = make_storage_key()
    modified = datetime.now(UTC) - timedelta(days=2)
    put_orphan(private_storage, real_key, modified_at=modified)
    alias_path = private_storage.root / alias_key
    alias_path.parent.mkdir(parents=True, exist_ok=True)
    alias_path.symlink_to(private_storage.root / real_key)

    listed = private_storage.list_objects(f"receipts/{ORG_A}/")
    assert [item.key for item in listed] == [real_key]
    with pytest.raises(ReceiptStorageUnavailable):
        private_storage.delete(alias_key)
    assert private_storage.get(real_key) == b"private receipt orphan candidate"


def test_orphan_service_only_records_objects_from_the_bound_organization(
    private_storage: FilesystemReceiptStorage,
) -> None:
    key_a = make_storage_key()
    key_b = f"receipts/33333333-3333-4333-8333-333333333333/{uuid.uuid4()}/{uuid.uuid4()}"
    modified = datetime.now(UTC) - timedelta(days=2)
    put_orphan(private_storage, key_a, modified_at=modified)
    put_orphan(private_storage, key_b, modified_at=modified)

    report = reconcile(now=datetime.now(UTC), cleanup_enabled=False)
    assert report.objects_scanned == 1
    assert report.orphans_discovered == 1
    assert orphan_state(key_a).status == "DISCOVERED"
    with org_session(UUID(ORG_A)) as session:
        other_tenant_rows = session.scalars(
            select(ReceiptStorageOrphan).where(
                ReceiptStorageOrphan.storage_key == key_b
            )
        ).all()
    assert other_tenant_rows == []
