"""0044_visit_follow_up_lifecycle — promote confirmed visit commitments into auditable actions."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from migrations import helpers

revision = "0044_visit_follow_up_lifecycle"
down_revision = "0043_market_opp_visit_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_visit_records_org_visit_id",
        "visit_records",
        ["organization_id", "visit_id", "id"],
    )
    op.create_table(
        "visit_follow_ups",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("visit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_key", sa.Text(), nullable=False),
        sa.Column("source_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_agent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_index", sa.Integer(), nullable=True),
        sa.Column("source_title", sa.Text(), nullable=True),
        sa.Column("source_due_date", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("due_date", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False, server_default="OPEN"),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirmed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE",
            name="fk_visit_follow_ups_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="SET NULL",
            name="fk_visit_follow_ups_created_by_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id"], ["users.id"], ondelete="SET NULL",
            name="fk_visit_follow_ups_confirmed_by_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["completed_by_user_id"], ["users.id"], ondelete="SET NULL",
            name="fk_visit_follow_ups_completed_by_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "trip_id", "visit_id"],
            ["visits.organization_id", "visits.trip_id", "visits.id"],
            ondelete="CASCADE",
            name="fk_visit_follow_ups_visit_same_trip",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "visit_id", "source_record_id"],
            ["visit_records.organization_id", "visit_records.visit_id", "visit_records.id"],
            ondelete="CASCADE",
            name="fk_visit_follow_ups_source_record",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "source_agent_run_id"],
            ["agent_runs.organization_id", "agent_runs.id"],
            ondelete="RESTRICT",
            name="fk_visit_follow_ups_source_agent_run",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_visit_follow_ups_org_id"),
        sa.UniqueConstraint(
            "organization_id", "visit_id", "source_key",
            name="uq_visit_follow_ups_source_key",
        ),
        sa.CheckConstraint(
            "source_type IN ('RECORD','MINUTES_DRAFT','MANUAL')",
            name="ck_visit_follow_ups_source_type_allowed",
        ),
        sa.CheckConstraint(
            "status IN ('OPEN','IN_PROGRESS','COMPLETED','DISMISSED')",
            name="ck_visit_follow_ups_status_allowed",
        ),
        sa.CheckConstraint(
            "length(btrim(title)) > 0",
            name="ck_visit_follow_ups_title_not_blank",
        ),
        sa.CheckConstraint(
            "(source_type = 'RECORD' AND source_record_id IS NOT NULL "
            "AND source_agent_run_id IS NULL AND source_index IS NOT NULL AND source_index >= 0) OR "
            "(source_type = 'MINUTES_DRAFT' AND source_record_id IS NULL "
            "AND source_agent_run_id IS NOT NULL AND source_index IS NOT NULL AND source_index >= 0) OR "
            "(source_type = 'MANUAL' AND source_record_id IS NULL "
            "AND source_agent_run_id IS NULL AND source_index IS NULL)",
            name="ck_visit_follow_ups_source_reference_consistent",
        ),
    )
    op.create_index(
        "ix_visit_follow_ups_organization_id", "visit_follow_ups", ["organization_id"]
    )
    op.create_index(
        "ix_visit_follow_ups_org_visit_status", "visit_follow_ups",
        ["organization_id", "visit_id", "status"],
    )
    op.create_index(
        "ix_visit_follow_ups_org_trip_status", "visit_follow_ups",
        ["organization_id", "trip_id", "status"],
    )
    helpers.enable_org_rls("visit_follow_ups", force=True)
    helpers.ensure_updated_at_trigger("visit_follow_ups")

    op.create_table(
        "visit_follow_up_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("follow_up_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("from_status", sa.Text(), nullable=True),
        sa.Column("to_status", sa.Text(), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE",
            name="fk_visit_follow_up_events_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="SET NULL",
            name="fk_visit_follow_up_events_actor_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "follow_up_id"],
            ["visit_follow_ups.organization_id", "visit_follow_ups.id"],
            ondelete="CASCADE",
            name="fk_visit_follow_up_events_follow_up",
        ),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_visit_follow_up_events_org_id"
        ),
        sa.CheckConstraint(
            "event_type IN ('ACCEPTED','DISMISSED','CREATED_MANUAL','STARTED',"
            "'COMPLETED','REOPENED','UPDATED')",
            name="ck_visit_follow_up_events_event_type_allowed",
        ),
    )
    op.create_index(
        "ix_visit_follow_up_events_organization_id",
        "visit_follow_up_events", ["organization_id"],
    )
    op.create_index(
        "ix_visit_follow_up_events_org_follow_up_created",
        "visit_follow_up_events", ["organization_id", "follow_up_id", "created_at"],
    )
    helpers.enable_org_rls("visit_follow_up_events", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("visit_follow_up_events")
    helpers.disable_org_rls("visit_follow_ups")
    op.drop_table("visit_follow_up_events")
    op.drop_table("visit_follow_ups")
    op.drop_constraint(
        "uq_visit_records_org_visit_id", "visit_records", type_="unique"
    )
