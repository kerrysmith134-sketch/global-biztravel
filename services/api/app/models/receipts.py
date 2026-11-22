"""私有原始票据资产。二进制内容由私有对象存储承载，数据库只保存稳定身份与校验元数据。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, uuid_pk

RECEIPT_STORAGE_STATUSES = ("AVAILABLE", "QUARANTINED", "DELETE_PENDING")


class ReceiptAsset(Base):
    """Trip 作用域内的不可变原始票据资产。storage_key 永不暴露给客户端。"""

    __tablename__ = "receipt_assets"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "trip_id"],
            ["trips.organization_id", "trips.id"],
            ondelete="CASCADE",
            name="fk_receipt_assets_trip_same_org",
        ),
        UniqueConstraint("organization_id", "id", name="uq_receipt_assets_org_id"),
        UniqueConstraint(
            "storage_provider", "storage_key", name="uq_receipt_assets_storage_object"
        ),
        UniqueConstraint(
            "organization_id", "trip_id", "id", name="uq_receipt_assets_org_trip_id"
        ),
        CheckConstraint("byte_length > 0", name="byte_length_positive"),
        CheckConstraint("length(sha256) = 64", name="sha256_length"),
        CheckConstraint(
            "storage_status IN ('AVAILABLE','QUARANTINED','DELETE_PENDING')",
            name="storage_status_allowed",
        ),
        CheckConstraint("storage_provider IN ('filesystem','s3')", name="storage_provider_allowed"),
        Index("ix_receipt_assets_org_trip_created", "organization_id", "trip_id", "created_at"),
        Index("ix_receipt_assets_org_trip_sha256", "organization_id", "trip_id", "sha256"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    storage_provider: Mapped[str] = mapped_column(Text, nullable=False)
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    byte_length: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    storage_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="AVAILABLE"
    )
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = created_at()


__all__ = ["RECEIPT_STORAGE_STATUSES", "ReceiptAsset"]
