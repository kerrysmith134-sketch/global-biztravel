"""0050_flight_operational_updates — immutable airline / airport operations events."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from migrations import helpers

revision = "0050_flight_operational_updates"
down_revision = "0049_flight_segment_bookings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flight_operational_updates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("flight_segment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False),
        sa.Column("provider_event_id", sa.Text(), nullable=False),
        sa.Column("update_type", sa.Text(), nullable=False),
        sa.Column("flight_designator", sa.Text(), nullable=False),
        sa.Column("departure_airport_iata", sa.Text(), nullable=False),
        sa.Column("arrival_airport_iata", sa.Text(), nullable=False),
        sa.Column("estimated_departure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_arrival_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_arrival_airport_iata", sa.Text(), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("source_payload_sha256", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["organization_id", "trip_id", "flight_segment_id"],
            ["trip_flight_segments.organization_id", "trip_flight_segments.trip_id", "trip_flight_segments.id"],
            ondelete="CASCADE",
            name="fk_flight_operational_updates_segment_same_trip",
        ),
        sa.UniqueConstraint(
            "organization_id", "provider", "provider_event_id",
            name="uq_flight_operational_updates_org_provider_event",
        ),
        sa.UniqueConstraint(
            "organization_id", "trip_id", "id",
            name="uq_flight_operational_updates_org_trip_id",
        ),
        sa.CheckConstraint(
            "update_type IN ('SCHEDULE_CHANGE','DEPARTURE_DELAY','ARRIVAL_DELAY','CANCELLED','DIVERTED','GATE_CHANGE')",
            name="ck_flight_operational_updates_update_type_allowed",
        ),
        sa.CheckConstraint("length(btrim(provider)) BETWEEN 1 AND 100", name="ck_flight_operational_updates_provider_length"),
        sa.CheckConstraint("length(btrim(provider_event_id)) BETWEEN 1 AND 160", name="ck_flight_operational_updates_provider_event_id_length"),
        sa.CheckConstraint("flight_designator ~ '^[A-Z0-9]{2,12}$'", name="ck_flight_operational_updates_flight_designator_format"),
        sa.CheckConstraint("departure_airport_iata ~ '^[A-Z]{3}$'", name="ck_flight_operational_updates_departure_airport_iata_format"),
        sa.CheckConstraint("arrival_airport_iata ~ '^[A-Z]{3}$'", name="ck_flight_operational_updates_arrival_airport_iata_format"),
        sa.CheckConstraint(
            "reported_arrival_airport_iata IS NULL OR reported_arrival_airport_iata ~ '^[A-Z]{3}$'",
            name="ck_flight_operational_updates_reported_arrival_airport_iata_format",
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_flight_operational_updates_confidence_range"),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_until > observed_at",
            name="ck_flight_operational_updates_validity_window_order",
        ),
        sa.CheckConstraint(
            "estimated_departure_at IS NULL OR estimated_arrival_at IS NULL OR estimated_arrival_at > estimated_departure_at",
            name="ck_flight_operational_updates_estimated_schedule_order",
        ),
        sa.CheckConstraint("content_sha256 ~ '^[a-f0-9]{64}$'", name="ck_flight_operational_updates_content_sha256_format"),
        sa.CheckConstraint(
            "source_payload_sha256 IS NULL OR source_payload_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_flight_operational_updates_source_payload_sha256_format",
        ),
    )
    op.create_index(
        "ix_flight_operational_updates_org_trip_segment_observed",
        "flight_operational_updates",
        ["organization_id", "trip_id", "flight_segment_id", "observed_at"],
    )
    helpers.enable_org_rls("flight_operational_updates", force=True)

    op.execute("""
    CREATE FUNCTION reject_flight_operational_update_update()
    RETURNS trigger AS $$
    BEGIN
        RAISE EXCEPTION 'flight_operational_updates are immutable'
            USING ERRCODE = '55000';
    END;
    $$ LANGUAGE plpgsql
    """)
    op.execute("""
    CREATE TRIGGER trg_flight_operational_updates_immutable
    BEFORE UPDATE ON flight_operational_updates
    FOR EACH ROW EXECUTE FUNCTION reject_flight_operational_update_update()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_flight_operational_updates_immutable ON flight_operational_updates")
    op.execute("DROP FUNCTION IF EXISTS reject_flight_operational_update_update()")
    helpers.disable_org_rls("flight_operational_updates")
    op.drop_table("flight_operational_updates")
