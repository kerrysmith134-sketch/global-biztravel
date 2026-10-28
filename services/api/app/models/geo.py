"""地点与客户域模型。对应迁移 ``0002_trip_domain``。

说明：
- ``places.geog``（PostGIS ``geography(Point,4326)``）是**派生列**，由数据库触发器
  ``trg_places_geog`` 从 ``lat``/``lon`` 维护，不进入 ORM 映射，也不参与模型/库漂移比对
  （见 ``migrations/env.py`` 的 ``include_object``）。空间查询一律走原生 SQL + PostGIS 函数。
- 坐标缺失是**合法状态**（``geocode_status = 'MISSING'``）：导入阶段只标记问题，不阻断导入；
  但它会让规划引擎把该拜访放入 ``unplaced``，因此必须在净空报告的 ``COORDINATES_MISSING``
  里显式暴露。
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk
from app.models.encrypted import EncryptedText


class Place(Base):
    __tablename__ = "places"
    __table_args__ = (
        CheckConstraint("lat IS NULL OR (lat >= -90 AND lat <= 90)", name="lat_range"),
        CheckConstraint("lon IS NULL OR (lon >= -180 AND lon <= 180)", name="lon_range"),
        CheckConstraint(
            "geocode_status IN ('MISSING','RESOLVED','FAILED')", name="geocode_status_allowed"
        ),
        Index("ix_places_organization_id_country_code", "organization_id", "country_code"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    address_line: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)
    country_code: Mapped[str | None] = mapped_column(CHAR(2))
    timezone: Mapped[str | None] = mapped_column(Text)
    lat: Mapped[float | None] = mapped_column()
    lon: Mapped[float | None] = mapped_column()
    geocode_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="MISSING")
    geocode_source: Mapped[str | None] = mapped_column(Text)
    geocode_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint("tier IN ('A','B','C')", name="tier_allowed"),
        CheckConstraint("status IN ('ACTIVE','INACTIVE','PROSPECT')", name="status_allowed"),
        Index(
            "uq_customers_organization_id_external_ref",
            "organization_id",
            "external_ref",
            unique=True,
            postgresql_where=text("external_ref IS NOT NULL"),
        ),
        Index("ix_customers_organization_id_name", "organization_id", "name"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    external_ref: Mapped[str | None] = mapped_column(Text)
    tier: Mapped[str] = mapped_column(Text, nullable=False, server_default="C")
    country_code: Mapped[str | None] = mapped_column(CHAR(2))
    industry: Mapped[str | None] = mapped_column(Text)
    primary_contact_name: Mapped[str | None] = mapped_column(EncryptedText())
    primary_contact_email: Mapped[str | None] = mapped_column(EncryptedText())
    primary_contact_phone: Mapped[str | None] = mapped_column(EncryptedText())
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="ACTIVE")
    notes: Mapped[str | None] = mapped_column(Text)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class CustomerPlace(Base):
    __tablename__ = "customer_places"
    __table_args__ = (
        UniqueConstraint("customer_id", "place_id"),
        Index("ix_customer_places_organization_id", "organization_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("customers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    place_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("places.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str | None] = mapped_column(Text)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = created_at()