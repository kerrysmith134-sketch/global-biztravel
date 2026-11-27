"""0053_trip_risk_cases — unify trip-level operational risks and recovery lifecycle."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from migrations import helpers

revision = "0053_trip_risk_cases"
down_revision = "0052_recovery_reassessment_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trip_risk_cases",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_key", sa.Text(), nullable=False),
        sa.Column("risk_type", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="OPEN"),
        sa.Column("severity", sa.Text(), nullable=False, server_default="MEDIUM"),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_refs", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("evidence_refs", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("context", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "last_observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("next_review_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("waiting_since", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_basis", sa.Text(), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_trip_risk_cases_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "trip_id"],
            ["trips.organization_id", "trips.id"],
            ondelete="CASCADE",
            name="fk_trip_risk_cases_trip_same_org",
        ),
        sa.UniqueConstraint(
            "organization_id", "trip_id", "id",
            name="uq_trip_risk_cases_org_trip_id",
        ),
        sa.UniqueConstraint(
            "organization_id", "trip_id", "case_key",
            name="uq_trip_risk_cases_org_trip_case_key",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','ACKNOWLEDGED','IN_PROGRESS','WAITING_EXTERNAL','ESCALATED','RESOLVED','DISMISSED')",
            name="ck_trip_risk_cases_status_allowed",
        ),
        sa.CheckConstraint(
            "severity IN ('LOW','MEDIUM','HIGH','CRITICAL')",
            name="ck_trip_risk_cases_severity_allowed",
        ),
        sa.CheckConstraint("version >= 1", name="ck_trip_risk_cases_version_positive"),
        sa.CheckConstraint("length(btrim(case_key)) > 0", name="ck_trip_risk_cases_case_key_not_blank"),
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_trip_risk_cases_title_not_blank"),
    )
    op.create_index(
        "ix_trip_risk_cases_organization_id",
        "trip_risk_cases",
        ["organization_id"],
    )
    op.create_index(
        "ix_trip_risk_cases_trip_id",
        "trip_risk_cases",
        ["trip_id"],
    )
    op.create_index(
        "ix_trip_risk_cases_org_trip_status_review",
        "trip_risk_cases",
        ["organization_id", "trip_id", "status", "next_review_at"],
    )
    op.create_index(
        "ix_trip_risk_cases_org_status_review",
        "trip_risk_cases",
        ["organization_id", "status", "next_review_at"],
    )
    helpers.enable_org_rls("trip_risk_cases", force=True)
    helpers.ensure_updated_at_trigger("trip_risk_cases")

    op.create_table(
        "trip_risk_case_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("actor_type", sa.Text(), nullable=False, server_default="SYSTEM"),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_trip_risk_case_events_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_trip_risk_case_events_actor_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "trip_id", "case_id"],
            ["trip_risk_cases.organization_id", "trip_risk_cases.trip_id", "trip_risk_cases.id"],
            ondelete="CASCADE",
            name="fk_trip_risk_case_events_case_same_trip",
        ),
        sa.CheckConstraint(
            "event_type IN ('OPENED','OBSERVED','SOURCE_LINKED','ACKNOWLEDGED','STARTED',"
            "'WAITING_EXTERNAL','ESCALATED','RESOLVED','DISMISSED','REOPENED','REVIEWED')",
            name="ck_trip_risk_case_events_event_type_allowed",
        ),
        sa.CheckConstraint(
            "actor_type IN ('SYSTEM','USER')",
            name="ck_trip_risk_case_events_actor_type_allowed",
        ),
    )
    op.create_index(
        "ix_trip_risk_case_events_organization_id",
        "trip_risk_case_events",
        ["organization_id"],
    )
    op.create_index(
        "ix_trip_risk_case_events_org_trip_case_created",
        "trip_risk_case_events",
        ["organization_id", "trip_id", "case_id", "created_at"],
    )
    helpers.enable_org_rls("trip_risk_case_events", force=True)
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_trip_risk_case_event_update() RETURNS trigger AS $
        BEGIN
            RAISE EXCEPTION 'trip_risk_case_events is append-only'
                USING ERRCODE = '55000';
        END
        $ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_trip_risk_case_events_append_only
        BEFORE UPDATE ON trip_risk_case_events
        FOR EACH ROW EXECUTE FUNCTION prevent_trip_risk_case_event_update()
        """
    )
    if helpers.role_exists("gbt_app"):
        op.execute("REVOKE UPDATE, DELETE ON trip_risk_case_events FROM gbt_app")


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_trip_risk_case_events_append_only ON trip_risk_case_events"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_trip_risk_case_event_update()")
    helpers.disable_org_rls("trip_risk_case_events")
    helpers.disable_org_rls("trip_risk_cases")
    op.drop_table("trip_risk_case_events")
    op.drop_table("trip_risk_cases")
