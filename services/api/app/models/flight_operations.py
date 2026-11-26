"""Immutable normalized airline / airport operational events for a booked flight segment."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, uuid_pk

FLIGHT_OPERATIONAL_UPDATE_TYPES = (
    "SCHEDULE_CHANGE",
    "DEPARTURE_DELAY",
    "ARRIVAL_DELAY",
    "CANCELLED",
    "DIVERTED",
    "GATE_CHANGE",
)


class FlightOperationalUpdate(Base):
    """Provider-reported operational fact; append-only and distinct from aircraft telemetry."""

    __tablename__ = "flight_operational_updates"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "provider",
            "provider_event_id",
            name="uq_flight_operational_updates_org_provider_event",
        ),
        UniqueConstraint("organization_id", "trip_id", "id", name="uq_flight_operational_updates_org_trip_id"),
        ForeignKeyConstraint(
            ["organization_id", "trip_id", "flight_segment_id"],
            [
                "trip_flight_segments.organization_id",
                "trip_flight_segments.trip_id",
                "trip_flight_segments.id",
            ],
            ondelete="CASCADE",
            name="fk_flight_operational_updates_segment_same_trip",
        ),
        CheckConstraint(
            "update_type IN ('SCHEDULE_CHANGE','DEPARTURE_DELAY','ARRIVAL_DELAY','CANCELLED','DIVERTED','GATE_CHANGE')",
            name="update_type_allowed",
        ),
        CheckConstraint("length(btrim(provider)) BETWEEN 1 AND 100", name="provider_length"),
        CheckConstraint("length(btrim(provider_event_id)) BETWEEN 1 AND 160", name="provider_event_id_length"),
        CheckConstraint("flight_designator ~ '^[A-Z0-9]{2,12}$'", name="flight_designator_format"),
        CheckConstraint("departure_airport_iata ~ '^[A-Z]{3}$'", name="departure_airport_iata_format"),
        CheckConstraint("arrival_airport_iata ~ '^[A-Z]{3}$'", name="arrival_airport_iata_format"),
        CheckConstraint(
            "reported_arrival_airport_iata IS NULL OR reported_arrival_airport_iata ~ '^[A-Z]{3}$'",
            name="reported_arrival_airport_iata_format",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint(
            "valid_until IS NULL OR valid_until > observed_at",
            name="validity_window_order",
        ),
        CheckConstraint(
            "estimated_departure_at IS NULL OR estimated_arrival_at IS NULL "
            "OR estimated_arrival_at > estimated_departure_at",
            name="estimated_schedule_order",
        ),
        CheckConstraint("content_sha256 ~ '^[a-f0-9]{64}$'", name="content_sha256_format"),
        CheckConstraint(
            "source_payload_sha256 IS NULL OR source_payload_sha256 ~ '^[a-f0-9]{64}$'",
            name="source_payload_sha256_format",
        ),
        Index(
            "ix_flight_operational_updates_org_trip_segment_observed",
            "organization_id",
            "trip_id",
            "flight_segment_id",
            "observed_at",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    trip_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    flight_segment_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    provider_event_id: Mapped[str] = mapped_column(Text, nullable=False)
    update_type: Mapped[str] = mapped_column(Text, nullable=False)
    flight_designator: Mapped[str] = mapped_column(Text, nullable=False)
    departure_airport_iata: Mapped[str] = mapped_column(Text, nullable=False)
    arrival_airport_iata: Mapped[str] = mapped_column(Text, nullable=False)
    estimated_departure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    estimated_arrival_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reported_arrival_airport_iata: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    source_payload_sha256: Mapped[str | None] = mapped_column(Text)
    content_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at()


__all__ = ["FLIGHT_OPERATIONAL_UPDATE_TYPES", "FlightOperationalUpdate"]
