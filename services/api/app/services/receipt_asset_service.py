"""票据资产领域服务：先持久化原件，再登记元数据；所有读取均受组织/Trip 作用域约束。"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import NotFound, ServiceUnavailable
from app.models.receipts import ReceiptAsset
from app.services import trip_service
from app.services.receipt_storage_service import (
    ReceiptStorageUnavailable,
    get_receipt_storage,
)


def create_receipt_asset(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID | str,
    *,
    original_filename: str,
    mime_type: str,
    content: bytes,
    sha256: str,
    actor_user_id: UUID | str,
) -> ReceiptAsset:
    trip = trip_service.require_trip(session, organization_id, trip_id)
    asset_id = uuid.uuid4()
    storage = get_receipt_storage()
    storage_key = f"receipts/{organization_id}/{trip.id}/{asset_id}"
    try:
        storage.put(storage_key, content, mime_type)
    except ReceiptStorageUnavailable as exc:
        raise ServiceUnavailable(str(exc)) from exc

    asset = ReceiptAsset(
        id=asset_id,
        organization_id=UUID(str(organization_id)),
        trip_id=trip.id,
        storage_provider=storage.provider,
        storage_key=storage_key,
        original_filename=original_filename,
        mime_type=mime_type,
        byte_length=len(content),
        sha256=sha256,
        storage_status="AVAILABLE",
        uploaded_by_user_id=UUID(str(actor_user_id)),
        uploaded_at=datetime.now(UTC),
    )
    try:
        session.add(asset)
        session.flush()
    except Exception:
        # Best-effort compensation for a failed DB flush. A later commit failure is
        # handled by the storage orphan-reconciliation task added in the next slice.
        try:
            storage.delete(storage_key)
        except ReceiptStorageUnavailable:
            pass
        raise
    return asset


def require_receipt_asset(
    session: Session,
    organization_id: UUID | str,
    asset_id: UUID | str,
) -> ReceiptAsset:
    asset = session.scalars(
        select(ReceiptAsset).where(
            ReceiptAsset.organization_id == str(organization_id),
            ReceiptAsset.id == str(asset_id),
            ReceiptAsset.storage_status == "AVAILABLE",
        )
    ).one_or_none()
    if asset is None:
        raise NotFound("票据资产不存在或不可用", details={"receipt_asset_id": str(asset_id)})
    return asset


def read_receipt_content(
    session: Session,
    organization_id: UUID | str,
    asset_id: UUID | str,
) -> tuple[ReceiptAsset, bytes]:
    asset = require_receipt_asset(session, organization_id, asset_id)
    try:
        content = get_receipt_storage(asset.storage_provider).get(asset.storage_key)
    except ReceiptStorageUnavailable as exc:
        raise ServiceUnavailable(str(exc)) from exc
    return asset, content


__all__ = ["create_receipt_asset", "read_receipt_content", "require_receipt_asset"]
