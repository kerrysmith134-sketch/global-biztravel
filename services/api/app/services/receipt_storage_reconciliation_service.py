"""Reconcile private receipt objects left behind by interrupted object/DB writes.

Receipt uploads write the binary object before inserting its metadata row. A process or
transaction failure after the object PUT can therefore leave an unreferenced private object.
This service only removes objects older than a conservative grace period and only within
the current tenant's storage prefix.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.receipts import ReceiptAsset
from app.services.receipt_storage_service import (
    ReceiptStorage,
    ReceiptStorageUnavailable,
    get_receipt_storage,
)


@dataclass(frozen=True)
class ReceiptReconciliationReport:
    objects_scanned: int = 0
    orphaned_objects: int = 0
    deleted_objects: int = 0
    failed_deletions: int = 0
    cutoff: datetime | None = None
    dry_run: bool = False


def reconcile_orphaned_receipt_objects(
    session: Session,
    organization_id: UUID | str,
    *,
    storage: ReceiptStorage | None = None,
    now: datetime | None = None,
    grace_seconds: int = 86400,
    dry_run: bool = False,
) -> ReceiptReconciliationReport:
    """Delete old storage objects that have no ReceiptAsset row for this organization.

    The tenant-specific prefix is used for the object listing and the DB query is constrained
    by both tenant and active storage provider. A one-day minimum grace period protects uploads
    whose object PUT succeeded but whose metadata transaction has not committed yet.
    """
    if grace_seconds < 3600:
        raise ValueError("receipt orphan grace period must be at least one hour")

    organization = UUID(str(organization_id))
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("reconciliation time must be timezone-aware")
    current = current.astimezone(UTC)
    cutoff = current - timedelta(seconds=grace_seconds)

    selected_storage = storage or get_receipt_storage()
    prefix = f"receipts/{organization}/"
    stored_objects = selected_storage.list_objects(prefix)

    known_keys = set(
        session.scalars(
            select(ReceiptAsset.storage_key).where(
                ReceiptAsset.organization_id == organization,
                ReceiptAsset.storage_provider == selected_storage.provider,
            )
        )
    )

    orphaned = 0
    deleted = 0
    failed = 0
    for stored_object in stored_objects:
        # Defense in depth: a misbehaving adapter must not let one tenant's scan cross prefixes.
        if not stored_object.key.startswith(prefix):
            continue
        modified = stored_object.last_modified
        if modified.tzinfo is None:
            modified = modified.replace(tzinfo=UTC)
        else:
            modified = modified.astimezone(UTC)
        if modified >= cutoff or stored_object.key in known_keys:
            continue

        orphaned += 1
        if dry_run:
            continue
        try:
            selected_storage.delete(stored_object.key)
            deleted += 1
        except ReceiptStorageUnavailable:
            failed += 1

    return ReceiptReconciliationReport(
        objects_scanned=len(stored_objects),
        orphaned_objects=orphaned,
        deleted_objects=deleted,
        failed_deletions=failed,
        cutoff=cutoff,
        dry_run=dry_run,
    )


__all__ = [
    "ReceiptReconciliationReport",
    "reconcile_orphaned_receipt_objects",
]
