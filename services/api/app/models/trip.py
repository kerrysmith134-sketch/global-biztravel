"""出行（Trip）域模型：Trips / Visits / Excel 导入批次与行。对应迁移 ``0002_trip_domain``。

几个刻意为之的决定：

- ``trips.active_plan_version_id`` 在 0002 只建列、**不建外键**：``plan_versions`` 要到
  ``0004_planning_reliability`` 才存在。外键在 0004 用约定名
  ``fk_trips_active_plan_version_id_plan_versions`` 补上（``ON DELETE SET NULL``：
  计划版本被清理时出行不应连带消失），模型侧同步声明，保证 ``alembic check`` 在 head 上干净。
- ``trips.version`` 是乐观并发令牌（对应 ``If-Match``），任何写操作递增，激活计划时用它
  检测"计划生成期间 Trip 已被改"。
- ``import_batches`` 与 ``import_rows`` 让 Excel 导入成为**两阶段、可审计**的过程：
  第一阶段只解析与校验（不落业务数据），第二阶段按用户确认的行号落库。
- ``visits.client_updated_at`` 是离线同步冲突判定（last-writer-wins）的依据（第 10.5 章）。
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk

TRIP_STATUSES = ("DRAFT", "PLANNING", "READY", "ACTIVE", "HOLD", "COMPLETED", "CANCELLED")
VISIT_STATUSES = ("PLANNED", "SCHEDULED", "DONE", "MISSED", "CANCELLED")
MOVEMENT_MODES = ("GROUND", "RAIL", "AIR", "SEA")
IMPORT_BATCH_STATUSES = ("PARSED", "CONFIRMED", "DISCARDED", "FAILED")
IMPORT_ROW_STATUSES = ("VALID", "INVALID", "DUPLICATE", "ACCEPTED", "REJECTED", "SKIPPED")


class Trip(Base):
    __tablename__ = "trips"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_trips_org_id"),
        CheckConstraint("window_end > window_start", name="window_order"),
        CheckConstraint(
            "status IN ('DRAFT','PLANNING','READY','ACTIVE','HOLD','COMPLETED','CANCELLED')",
            name="status_allowed",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        Index("ix_trips_organization_id_status", "organization_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str | None] = mapped_column(Text)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    origin_place_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("places.id", ondelete="SET NULL")
    )
    home_timezone: Mapped[str] = mapped_column(Text, nullable=False, server_default="Asia/Shanghai")
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="CNY")
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    countries: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="DRAFT")
    budget_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    active_plan_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("plan_versions.id", ondelete="SET NULL", use_alter=True),
        index=True,
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Visit(Base):
    __tablename__ = "visits"
    __table_args__ = (
        CheckConstraint("service_minutes > 0", name="service_minutes_positive"),
        CheckConstraint("priority BETWEEN 1 AND 3", name="priority_range"),
        CheckConstraint("business_value >= 0", name="business_value_non_negative"),
        CheckConstraint("movement_mode IN ('GROUND','RAIL','AIR','SEA')", name="movement_mode_allowed"),
        CheckConstraint(
            "status IN ('PLANNED','SCHEDULED','DONE','MISSED','CANCELLED')", name="status_allowed"
        ),
        CheckConstraint(
            "time_window_start IS NULL OR time_window_end IS NULL OR time_window_end > time_window_start",
            name="time_window_order",
        ),
        Index("ix_visits_organization_id_status", "organization_id", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    place_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("places.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    title: Mapped[str | None] = mapped_column(Text)
    objective: Mapped[str | None] = mapped_column(Text)
    service_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default="60")
    time_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    time_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="2")
    business_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False, server_default="0")
    movement_mode: Mapped[str] = mapped_column(Text, nullable=False, server_default="GROUND")
    is_immovable: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="PLANNED")
    client_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class ImportBatch(Base):
    __tablename__ = "import_batches"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PARSED','CONFIRMED','DISCARDED','FAILED')", name="status_allowed"
        ),
        Index("ix_import_batches_trip_id_created_at", "trip_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trip_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="PARSED")
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    valid_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    invalid_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    duplicate_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    report: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    parsed_at: Mapped[datetime] = created_at()
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class ImportRow(Base):
    __tablename__ = "import_rows"
    __table_args__ = (
        UniqueConstraint("batch_id", "row_number"),
        CheckConstraint(
            "status IN ('VALID','INVALID','DUPLICATE','ACCEPTED','REJECTED','SKIPPED')",
            name="status_allowed",
        ),
        Index("ix_import_rows_batch_id_row_number", "batch_id", "row_number"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False, index=True
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    normalized: Mapped[dict | None] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="VALID")
    issues: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    customer_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    place_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("places.id", ondelete="SET NULL")
    )
    visit_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("visits.id", ondelete="SET NULL")
    )