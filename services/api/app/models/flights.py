"""Human-confirmed flight itinerary segments and versioned plan bindings.

A segment is a booking fact, not a plan stop. A binding is a separate, auditable
user decision that connects one segment to an AIR PlanTravelLeg in one immutable
PlanVersion. No booking locator / PNR is stored in this domain model.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at, updated_at, uuid_pk

FLIGHT_BOOKING_STATUSES = ("TENTATIVE", "BOOKED", "TICKETED", "CANCELLED")
FLIGHT_SEGMENT_SOURCES = ("USER_ENTERED", "ITINERARY_IMPORT", "PROVIDER_IMPORT")
FLIGHT_BINDING_STATUSES = ("ACTIVE", "RELEASED")


class TripFlightSegment(Base):
    """One booked or tentative airline segment belonging to a trip."""

    __tablename__ = "trip_flight_segments"
    __table_args__ = (
        UniqueConstraint("organization_id", "trip_id", "id", name="uq_trip_flight_segments_org_trip_id"),
        UniqueConstraint(
            "organization_id",
            "trip_id",
            "flight_designator",
            "scheduled_departure_at",
            name="uq_trip_flight_segments_trip_flight_departure",
        ),
        CheckConstraint("length(btrim(flight_designator)) BETWEEN 2 AND 12", name="flight_designator_length"),
        CheckConstraint("departure_airport_iata ~ '^[A-Z]{3}$'", name="departure_airport_iata_format"),
        CheckConstraint("arrival_airport_iata ~ '^[A-Z]{3}$'", name="arrival_airport_iata_format"),
        CheckConstraint("departure_airport_iata <> arrival_airport_iata", name="airports_must_differ"),
        CheckConstraint("scheduled_arrival_at > scheduled_departure_at", name="schedule_order"),
        CheckConstraint(
            "booking_status IN ('TENTATIVE','BOOKED','TICKETED','CANCELLED')",
            name="booking_status_allowed",
        ),
        CheckConstraint(
            "source IN ('USER_ENTERED','ITINERARY_IMPORT','PROVIDER_IMPORT')",
            name="source_allowed",
        ),
        CheckConstraint(
            "(tracking_provider IS NULL) = (tracking_reference IS NULL)",
            name="tracking_reference_pair",
        ),
        ForeignKeyConstraint(
            ["organization_id", "trip_id"],
            ["trips.organization_id", "trips.id"],
            ondelete="CASCADE",
            name="fk_trip_flight_segments_trip_same_org",
        ),
        Index("ix_trip_flight_segments_organization_id", "organization_id"),
        Index(
            "ix_trip_flight_segments_org_trip_departure",
            "organization_id",
            "trip_id",
            "scheduled_departure_at",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    trip_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    flight_designator: Mapped[str] = mapped_column(Text, nullable=False)
    departure_airport_iata: Mapped[str] = mapped_column(Text, nullable=False)
    arrival_airport_iata: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_departure_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scheduled_arrival_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    departure_timezone: Mapped[str] = mapped_column(Text, nullable=False)
    arrival_timezone: Mapped[str] = mapped_column(Text, nullable=False)
    booking_status: Mapped[str] = mapped_column(Text, nullable=False, server_default="BOOKED")
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="USER_ENTERED")
    tracking_provider: Mapped[str | None] = mapped_column(Text)
    tracking_reference: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


class FlightSegmentPlanBinding(Base):
    """Append-preserving human confirmation linking a flight segment to one plan leg."""

    __tablename__ = "flight_segment_plan_bindings"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_flight_segment_plan_bindings_org_id"),
        CheckConstraint("status IN ('ACTIVE','RELEASED')", name="status_allowed"),
        CheckConstraint(
            "(status = 'ACTIVE' AND released_at IS NULL AND released_by_user_id IS NULL "
            "AND release_reason IS NULL) OR "
            "(status = 'RELEASED' AND released_at IS NOT NULL AND length(btrim(release_reason)) > 0)",
            name="release_fields_consistent",
        ),
        ForeignKeyConstraint(
            ["organization_id", "trip_id", "flight_segment_id"],
            [
                "trip_flight_segments.organization_id",
                "trip_flight_segments.trip_id",
                "trip_flight_segments.id",
            ],
            ondelete="CASCADE",
            name="fk_flight_segment_bindings_segment_same_trip",
        ),
        ForeignKeyConstraint(
            ["organization_id", "trip_id", "plan_version_id"],
            ["plan_versions.organization_id", "plan_versions.trip_id", "plan_versions.id"],
            ondelete="CASCADE",
            name="fk_flight_segment_bindings_plan_same_trip",
        ),
        ForeignKeyConstraint(
            ["organization_id", "plan_version_id", "plan_travel_leg_id"],
            [
                "plan_travel_legs.organization_id",
                "plan_travel_legs.plan_version_id",
                "plan_travel_legs.id",
            ],
            ondelete="CASCADE",
            name="fk_flight_segment_bindings_leg_same_plan",
        ),
        ForeignKeyConstraint(
            ["linked_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_flight_segment_bindings_linked_by_user_id_users",
        ),
        ForeignKeyConstraint(
            ["released_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_flight_segment_bindings_released_by_user_id_users",
        ),
        Index("ix_flight_segment_plan_bindings_organization_id", "organization_id"),
        Index(
            "uq_flight_segment_bindings_org_segment_active",
            "organization_id",
            "flight_segment_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
        ),
        Index(
            "ix_flight_segment_bindings_org_trip_created",
            "organization_id",
            "trip_id",
            "created_at",
        ),
        Index(
            "ix_flight_segment_bindings_org_plan_leg",
            "organization_id",
            "plan_version_id",
            "plan_travel_leg_id",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    organization_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    trip_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    flight_segment_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    plan_version_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    plan_travel_leg_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="ACTIVE")
    linked_by_user_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    released_by_user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    release_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()
    updated_at: Mapped[datetime] = updated_at()


__all__ = [
    "FLIGHT_BINDING_STATUSES",
    "FLIGHT_BOOKING_STATUSES",
    "FLIGHT_SEGMENT_SOURCES",
    "FlightSegmentPlanBinding",
    "TripFlightSegment",
]
