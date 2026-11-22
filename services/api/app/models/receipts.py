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
from sqlalchemy.dialects.postgresql import JSONB
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


class ReceiptOcrRun(Base):
    """One immutable-original, versioned OCR execution linked to its AgentRun."""

    __tablename__ = "receipt_ocr_runs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "receipt_asset_id"],
            ["receipt_assets.organization_id", "receipt_assets.id"],
            ondelete="RESTRICT",
            name="fk_receipt_ocr_runs_asset_same_org",
        ),
        ForeignKeyConstraint(
            ["organization_id", "agent_run_id"],
            ["agent_runs.organization_id", "agent_runs.id"],
            ondelete="RESTRICT",
            name="fk_receipt_ocr_runs_agent_run_same_org",
        ),
        UniqueConstraint("organization_id", "id", name="uq_receipt_ocr_runs_org_id"),
        UniqueConstraint("organization_id", "agent_run_id", name="uq_receipt_ocr_runs_agent"),
        CheckConstraint(
            "status IN ('RUNNING','SUCCEEDED','FAILED')", name="status_allowed"
        ),
        CheckConstraint("length(content_sha256) = 64", name="content_sha256_length"),
        CheckConstraint("length(btrim(engine)) > 0", name="engine_not_blank"),
        CheckConstraint("length(btrim(engine_version)) > 0", name="engine_version_not_blank"),
        Index("ix_receipt_ocr_runs_org_asset_created", "organization_id", "receipt_asset_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    receipt_asset_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    agent_run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    engine: Mapped[str] = mapped_column(Text, nullable=False)
    engine_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="RUNNING")
    candidates: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class ReceiptOcrReview(Base):
    """Append-only human decision over one exact OCR execution."""

    __tablename__ = "receipt_ocr_reviews"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "receipt_ocr_run_id"],
            ["receipt_ocr_runs.organization_id", "receipt_ocr_runs.id"],
            ondelete="RESTRICT",
            name="fk_receipt_ocr_reviews_run_same_org",
        ),
        UniqueConstraint("organization_id", "id", name="uq_receipt_ocr_reviews_org_id"),
        UniqueConstraint("organization_id", "receipt_ocr_run_id", name="uq_receipt_ocr_reviews_run"),
        CheckConstraint("decision IN ('CONFIRMED','REJECTED')", name="decision_allowed"),
        CheckConstraint(
            "(decision = 'CONFIRMED' AND confirmed_fields <> '{}'::jsonb) OR decision = 'REJECTED'",
            name="decision_fields_consistent",
        ),
        Index("ix_receipt_ocr_reviews_org_created", "organization_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    receipt_ocr_run_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_fields: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    note: Mapped[str | None] = mapped_column(Text)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = created_at()


__all__ = [
    "RECEIPT_STORAGE_STATUSES",
    "ReceiptAsset",
    "ReceiptOcrRun",
    "ReceiptOcrReview",
]
