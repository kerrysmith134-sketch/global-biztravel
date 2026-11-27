"""0054_trip_risk_event_sequence_guards — enforce append-only event sequence invariants."""

from alembic import op

from migrations import helpers

revision = "0054_trip_risk_event_sequence_guards"
down_revision = "0053_trip_risk_cases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Fail closed rather than silently rewriting existing audit history.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM trip_risk_case_events
                GROUP BY organization_id, trip_id, case_id, case_version
                HAVING COUNT(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'Cannot enforce unique Trip Risk event versions: duplicate case versions exist';
            END IF;
            IF EXISTS (
                SELECT 1
                FROM trip_risk_case_events
                WHERE case_version < 1
            ) THEN
                RAISE EXCEPTION
                    'Cannot enforce positive Trip Risk event versions: invalid rows exist';
            END IF;
        END
        $$;
        """
    )
    op.create_check_constraint(
        "ck_trip_risk_case_events_case_version_positive",
        "trip_risk_case_events",
        "case_version >= 1",
    )
    op.create_unique_constraint(
        "uq_trip_risk_case_events_org_trip_case_version",
        "trip_risk_case_events",
        ["organization_id", "trip_id", "case_id", "case_version"],
    )

    op.execute(
        "DROP TRIGGER IF EXISTS trg_trip_risk_case_events_append_only ON trip_risk_case_events"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_trip_risk_case_event_mutation() RETURNS trigger AS $$
        BEGIN
            -- Direct row deletion is forbidden. Preserve the schema's existing
            -- ON DELETE CASCADE semantics when a parent Trip/Case is deliberately removed.
            IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'trip_risk_case_events is append-only'
                USING ERRCODE = '55000';
        END
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_trip_risk_case_events_append_only
        BEFORE UPDATE OR DELETE ON trip_risk_case_events
        FOR EACH ROW EXECUTE FUNCTION prevent_trip_risk_case_event_mutation()
        """
    )
    if helpers.role_exists("gbt_app"):
        op.execute("REVOKE UPDATE, DELETE ON trip_risk_case_events FROM gbt_app")


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_trip_risk_case_events_append_only ON trip_risk_case_events"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_trip_risk_case_event_mutation()")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_trip_risk_case_event_update() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'trip_risk_case_events is append-only'
                USING ERRCODE = '55000';
        END
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_trip_risk_case_events_append_only
        BEFORE UPDATE ON trip_risk_case_events
        FOR EACH ROW EXECUTE FUNCTION prevent_trip_risk_case_event_update()
        """
    )
    op.drop_constraint(
        "uq_trip_risk_case_events_org_trip_case_version",
        "trip_risk_case_events",
        type_="unique",
    )
    op.drop_constraint(
        "ck_trip_risk_case_events_case_version_positive",
        "trip_risk_case_events",
        type_="check",
    )
