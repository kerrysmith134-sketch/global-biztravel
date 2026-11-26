"""0049_flight_segment_bookings — explicit itinerary records and auditable plan-leg bindings."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from migrations import helpers

revision = "0049_flight_segment_bookings"
down_revision = "0048_receipt_orphans"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_plan_versions_org_trip_id", "plan_versions", ["organization_id", "trip_id", "id"]
    )
    op.create_unique_constraint(
        "uq_plan_travel_legs_org_plan_id", "plan_travel_legs", ["organization_id", "plan_version_id", "id"]
    )

    op.create_table(
        "trip_flight_segments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("flight_designator", sa.Text(), nullable=False),
        sa.Column("departure_airport_iata", sa.Text(), nullable=False),
        sa.Column("arrival_airport_iata", sa.Text(), nullable=False),
        sa.Column("scheduled_departure_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scheduled_arrival_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("departure_timezone", sa.Text(), nullable=False),
        sa.Column("arrival_timezone", sa.Text(), nullable=False),
        sa.Column("booking_status", sa.Text(), nullable=False, server_default="BOOKED"),
        sa.Column("source", sa.Text(), nullable=False, server_default="USER_ENTERED"),
        sa.Column("tracking_provider", sa.Text(), nullable=True),
        sa.Column("tracking_reference", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["organization_id", "trip_id"], ["trips.organization_id", "trips.id"],
            ondelete="CASCADE", name="fk_trip_flight_segments_trip_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"],
            ondelete="SET NULL", name="fk_trip_flight_segments_created_by_user_id_users",
        ),
        sa.UniqueConstraint("organization_id", "trip_id", "id", name="uq_trip_flight_segments_org_trip_id"),
        sa.UniqueConstraint(
            "organization_id", "trip_id", "flight_designator", "scheduled_departure_at",
            name="uq_trip_flight_segments_trip_flight_departure",
        ),
        sa.CheckConstraint(
            "length(btrim(flight_designator)) BETWEEN 2 AND 12",
            name="ck_trip_flight_segments_flight_designator_length",
        ),
        sa.CheckConstraint(
            "departure_airport_iata ~ '^[A-Z]{3}$'",
            name="ck_trip_flight_segments_departure_airport_iata_format",
        ),
        sa.CheckConstraint(
            "arrival_airport_iata ~ '^[A-Z]{3}$'",
            name="ck_trip_flight_segments_arrival_airport_iata_format",
        ),
        sa.CheckConstraint(
            "departure_airport_iata <> arrival_airport_iata",
            name="ck_trip_flight_segments_airports_must_differ",
        ),
        sa.CheckConstraint(
            "scheduled_arrival_at > scheduled_departure_at",
            name="ck_trip_flight_segments_schedule_order",
        ),
        sa.CheckConstraint(
            "booking_status IN ('TENTATIVE','BOOKED','TICKETED','CANCELLED')",
            name="ck_trip_flight_segments_booking_status_allowed",
        ),
        sa.CheckConstraint(
            "source IN ('USER_ENTERED','ITINERARY_IMPORT','PROVIDER_IMPORT')",
            name="ck_trip_flight_segments_source_allowed",
        ),
        sa.CheckConstraint(
            "(tracking_provider IS NULL) = (tracking_reference IS NULL)",
            name="ck_trip_flight_segments_tracking_reference_pair",
        ),
    )
    op.create_index("ix_trip_flight_segments_organization_id", "trip_flight_segments", ["organization_id"])
    op.create_index(
        "ix_trip_flight_segments_org_trip_departure",
        "trip_flight_segments",
        ["organization_id", "trip_id", "scheduled_departure_at"],
    )
    helpers.enable_org_rls("trip_flight_segments", force=True)
    helpers.ensure_updated_at_trigger("trip_flight_segments")

    op.create_table(
        "flight_segment_plan_bindings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("flight_segment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_version_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_travel_leg_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="ACTIVE"),
        sa.Column("linked_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("released_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["organization_id", "trip_id", "flight_segment_id"],
            ["trip_flight_segments.organization_id", "trip_flight_segments.trip_id", "trip_flight_segments.id"],
            ondelete="CASCADE", name="fk_flight_segment_bindings_segment_same_trip",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "trip_id", "plan_version_id"],
            ["plan_versions.organization_id", "plan_versions.trip_id", "plan_versions.id"],
            ondelete="CASCADE", name="fk_flight_segment_bindings_plan_same_trip",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "plan_version_id", "plan_travel_leg_id"],
            ["plan_travel_legs.organization_id", "plan_travel_legs.plan_version_id", "plan_travel_legs.id"],
            ondelete="CASCADE", name="fk_flight_segment_bindings_leg_same_plan",
        ),
        sa.ForeignKeyConstraint(
            ["linked_by_user_id"], ["users.id"],
            ondelete="RESTRICT", name="fk_flight_segment_bindings_linked_by_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["released_by_user_id"], ["users.id"],
            ondelete="SET NULL", name="fk_flight_segment_bindings_released_by_user_id_users",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_flight_segment_plan_bindings_org_id"),
        sa.CheckConstraint("status IN ('ACTIVE','RELEASED')", name="ck_flight_segment_plan_bindings_status_allowed"),
        sa.CheckConstraint(
            "(status = 'ACTIVE' AND released_at IS NULL AND released_by_user_id IS NULL "
            "AND release_reason IS NULL) OR "
            "(status = 'RELEASED' AND released_at IS NOT NULL AND length(btrim(release_reason)) > 0)",
            name="ck_flight_segment_plan_bindings_release_fields_consistent",
        ),
    )
    op.create_index(
        "ix_flight_segment_plan_bindings_organization_id",
        "flight_segment_plan_bindings",
        ["organization_id"],
    )
    op.create_index(
        "uq_flight_segment_bindings_org_segment_active",
        "flight_segment_plan_bindings",
        ["organization_id", "flight_segment_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_flight_segment_bindings_org_trip_created",
        "flight_segment_plan_bindings",
        ["organization_id", "trip_id", "created_at"],
    )
    op.create_index(
        "ix_flight_segment_bindings_org_plan_leg",
        "flight_segment_plan_bindings",
        ["organization_id", "plan_version_id", "plan_travel_leg_id"],
    )
    helpers.enable_org_rls("flight_segment_plan_bindings", force=True)
    helpers.ensure_updated_at_trigger("flight_segment_plan_bindings")


def downgrade() -> None:
    helpers.disable_org_rls("flight_segment_plan_bindings")
    helpers.disable_org_rls("trip_flight_segments")
    op.drop_table("flight_segment_plan_bindings")
    op.drop_table("trip_flight_segments")
    op.drop_constraint("uq_plan_travel_legs_org_plan_id", "plan_travel_legs", type_="unique")
    op.drop_constraint("uq_plan_versions_org_trip_id", "plan_versions", type_="unique")
