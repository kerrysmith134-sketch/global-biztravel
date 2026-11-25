"""Reconcile private receipt objects against tenant-owned ReceiptAsset rows.

Cleanup is deliberately opt-in and two-clock gated: an object must have been
older than the configured grace period AND observed as an orphan for at least
that long. Upload and reconciliation share a PostgreSQL advisory lock per
organization, closing the object-PUT / metadata-COMMIT race.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.receipts import ReceiptAsset, ReceiptStorageOrphan
from app.services.receipt_storage_service import (
    ReceiptStorageUnavailable,
    get_receipt_storage,
)


@dataclass
class ReceiptReconciliationReport:
    objects_scanned: int = 0
    orphans_discovered: int = 0
    orphans_eligible: int = 0
    objects_deleted: int = 0
    delete_failures: int = 0
    resolved: int = 0
    missing: int = 0
    cleanup_enabled: bool = False
    errors: list[str] = field(default_factory=list)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _belongs_to_organization(key: str, organization_id: str) -> bool:
    """Accept only the canonical private receipt key shape for this tenant."""
    match = re.fullmatch(
        r"receipts/([0-9a-f-]{36})/([0-9a-f-]{36})/([0-9a-f-]{36})",
        key,
        re.IGNORECASE,
    )
    if match is None or match.group(1).lower() != organization_id:
        return False
    try:
        return all(str(UUID(part)) == part.lower() for part in match.groups())
    except ValueError:
        return False


def reconcile_receipt_storage(
    session: Session,
    organization_id: UUID | str,
    *,
    now: datetime | None = None,
    grace_seconds: int | None = None,
    cleanup_enabled: bool | None = None,
) -> ReceiptReconciliationReport:
    """Discover orphan objects and, when explicitly enabled, delete safe candidates.

    This function does not commit. The caller's tenant session owns the transaction.
    Storage deletion is idempotent; a DB commit failure after a successful external
    delete is recorded as MISSING on the next scan rather than falsely as DELETED.
    """
    settings = get_settings()
    org_id = str(UUID(str(organization_id)))
    current = _utc(now or session.scalar(select(func.clock_timestamp())))
    grace = max(
        0,
        settings.receipt_orphan_grace_seconds if grace_seconds is None else grace_seconds,
    )
    delete_enabled = (
        settings.receipt_orphan_cleanup_enabled if cleanup_enabled is None else cleanup_enabled
    )
    report = ReceiptReconciliationReport(cleanup_enabled=delete_enabled)

    storage = get_receipt_storage()
    provider = storage.provider
    prefix = f"receipts/{org_id}/"

    # Listing happens outside the DB lock to keep uploads fast. It is only a snapshot;
    # all destructive decisions happen after the shared tenant advisory lock is acquired.
    object_map = {item.key: item for item in storage.list_objects(prefix)}

    # Same lock as receipt_asset_service.create_receipt_asset. Upload holds this
    # transaction-scoped lock from before PUT through ReceiptAsset commit.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:organization_id, 0))"),
        {"organization_id": org_id},
    )

    known_assets = {
        asset.storage_key: asset
        for asset in session.scalars(
            select(ReceiptAsset).where(
                ReceiptAsset.organization_id == org_id,
                ReceiptAsset.storage_provider == provider,
            )
        )
    }
    known_keys = set(known_assets)

    # A new upload may finish between ListObjects and lock acquisition. For DB-backed
    # keys missing from the listing snapshot, HEAD the exact object before calling it
    # missing. This is metadata-only and never downloads the receipt bytes.
    for key, asset in known_assets.items():
        if key in object_map:
            continue
        if not _belongs_to_organization(key, org_id):
            report.errors.append(f"ReceiptAsset {asset.id} has a non-canonical storage key")
            continue
        try:
            stored = storage.stat_object(key)
        except ReceiptStorageUnavailable as exc:
            report.errors.append(f"{key}: metadata check failed: {str(exc)[:300]}")
            continue
        if stored is not None:
            object_map[key] = stored
            continue

        # A row may already exist from a prior orphan observation or a previous missing
        # reference diagnosis. Reuse it instead of creating duplicate audit history.
        incident = session.scalars(
            select(ReceiptStorageOrphan).where(
                ReceiptStorageOrphan.organization_id == org_id,
                ReceiptStorageOrphan.storage_provider == provider,
                ReceiptStorageOrphan.storage_key == key,
            )
        ).one_or_none()
        if incident is None:
            incident = ReceiptStorageOrphan(
                organization_id=UUID(org_id),
                storage_provider=provider,
                storage_key=key,
                object_size=asset.byte_length,
                object_last_modified_at=_utc(asset.uploaded_at),
                status="MISSING",
                first_seen_at=current,
                last_seen_at=current,
                last_error="ReceiptAsset metadata exists but its storage object is absent",
            )
            session.add(incident)
            report.missing += 1
        elif incident.status != "MISSING":
            incident.status = "MISSING"
            incident.last_seen_at = current
            incident.last_error = "ReceiptAsset metadata exists but its storage object is absent"
            report.missing += 1
        else:
            incident.last_seen_at = current

    objects = list(object_map.values())
    report.objects_scanned = len(objects)

    incidents = session.scalars(
        select(ReceiptStorageOrphan).where(
            ReceiptStorageOrphan.organization_id == org_id,
            ReceiptStorageOrphan.storage_provider == provider,
        )
    ).all()
    by_key = {row.storage_key: row for row in incidents}
    listed_keys: set[str] = set()
    cutoff = current - timedelta(seconds=grace)

    for stored in objects:
        if not _belongs_to_organization(stored.key, org_id):
            report.errors.append("storage listing returned a key outside the requested tenant prefix")
            continue
        if stored.size < 0:
            report.errors.append("storage listing returned a negative object size")
            continue

        key = stored.key
        listed_keys.add(key)
        incident = by_key.get(key)

        # Any metadata row, including quarantined/deletion-pending assets, is authoritative.
        if key in known_keys:
            if incident is not None and incident.status != "RESOLVED":
                incident.status = "RESOLVED"
                incident.last_error = None
                report.resolved += 1
            continue

        if incident is None:
            incident = ReceiptStorageOrphan(
                organization_id=UUID(org_id),
                storage_provider=provider,
                storage_key=key,
                object_size=stored.size,
                object_last_modified_at=_utc(stored.last_modified),
                status="DISCOVERED",
                first_seen_at=current,
                last_seen_at=current,
            )
            session.add(incident)
            by_key[key] = incident
            report.orphans_discovered += 1
        else:
            if incident.status == "DELETED":
                # Keys are immutable UUID identities. Ignore a listing snapshot captured
                # before a concurrent worker deleted this object; it cannot be a new upload.
                continue
            if incident.status in {"MISSING", "RESOLVED"}:
                # The same key reappeared. Start a new observation window; never inherit
                # a prior absence decision for a newly visible object.
                incident.status = "DISCOVERED"
                incident.first_seen_at = current
                incident.delete_attempts = 0
                incident.deleted_at = None
                incident.last_error = None
            incident.object_size = stored.size
            incident.object_last_modified_at = _utc(stored.last_modified)
            incident.last_seen_at = current

        object_is_old = _utc(stored.last_modified) <= cutoff
        orphan_is_old = _utc(incident.first_seen_at) <= cutoff
        if not (object_is_old and orphan_is_old):
            continue

        report.orphans_eligible += 1
        if not delete_enabled:
            continue

        # Recheck the authoritative row immediately before deletion while the shared
        # tenant advisory lock is still held. This also protects against future upload
        # paths accidentally bypassing the initial known_keys snapshot.
        asset_exists = session.scalar(
            select(ReceiptAsset.id).where(
                ReceiptAsset.organization_id == org_id,
                ReceiptAsset.storage_provider == provider,
                ReceiptAsset.storage_key == key,
            ).limit(1)
        )
        if asset_exists is not None:
            incident.status = "RESOLVED"
            incident.last_error = None
            report.resolved += 1
            continue

        incident.status = "DELETE_PENDING"
        incident.delete_attempts += 1
        try:
            storage.delete(key)
        except ReceiptStorageUnavailable as exc:
            incident.status = "DELETE_FAILED"
            incident.last_error = str(exc)[:1000]
            report.delete_failures += 1
            report.errors.append(f"{key}: {str(exc)[:300]}")
        else:
            incident.status = "DELETED"
            incident.deleted_at = current
            incident.last_error = None
            report.objects_deleted += 1

    # If a previously observed orphan disappears without a recorded successful
    # delete, retain the audit row as MISSING. Never infer that we deleted it.
    for incident in incidents:
        if incident.storage_key in listed_keys:
            continue
        if incident.storage_key in known_keys:
            continue
        if incident.status in {"DISCOVERED", "DELETE_PENDING", "DELETE_FAILED"}:
            incident.status = "MISSING"
            incident.last_error = "Object no longer appears in the tenant-scoped storage listing"
            report.missing += 1

    session.flush()
    return report


__all__ = ["ReceiptReconciliationReport", "reconcile_receipt_storage"]
