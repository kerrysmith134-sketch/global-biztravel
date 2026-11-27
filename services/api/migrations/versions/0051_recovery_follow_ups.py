"""0051_recovery_follow_ups — bind appointment recovery actions to the visit lifecycle."""

from alembic import op

revision = "0051_recovery_follow_ups"
down_revision = "0050_flight_operational_updates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_visit_follow_ups_source_type_allowed",
        "visit_follow_ups",
        type_="check",
    )
    op.create_check_constraint(
        "ck_visit_follow_ups_source_type_allowed",
        "visit_follow_ups",
        "source_type IN ('RECORD','MINUTES_DRAFT','APPOINTMENT_RECOVERY','MANUAL')",
    )
    op.drop_constraint(
        "ck_visit_follow_ups_source_reference_consistent",
        "visit_follow_ups",
        type_="check",
    )
    op.create_check_constraint(
        "ck_visit_follow_ups_source_reference_consistent",
        "visit_follow_ups",
        "(source_type = 'RECORD' AND source_record_id IS NOT NULL "
        "AND source_agent_run_id IS NULL AND source_index IS NOT NULL AND source_index >= 0) OR "
        "(source_type = 'MINUTES_DRAFT' AND source_record_id IS NULL "
        "AND source_agent_run_id IS NOT NULL AND source_index IS NOT NULL AND source_index >= 0) OR "
        "(source_type = 'APPOINTMENT_RECOVERY' AND source_record_id IS NULL "
        "AND source_agent_run_id IS NOT NULL AND source_index IS NOT NULL AND source_index >= 0) OR "
        "(source_type = 'MANUAL' AND source_record_id IS NULL "
        "AND source_agent_run_id IS NULL AND source_index IS NULL)",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM visit_follow_ups
                WHERE source_type = 'APPOINTMENT_RECOVERY'
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade while appointment recovery follow-ups exist';
            END IF;
        END $$;
        """
    )
    op.drop_constraint(
        "ck_visit_follow_ups_source_reference_consistent",
        "visit_follow_ups",
        type_="check",
    )
    op.create_check_constraint(
        "ck_visit_follow_ups_source_reference_consistent",
        "visit_follow_ups",
        "(source_type = 'RECORD' AND source_record_id IS NOT NULL "
        "AND source_agent_run_id IS NULL AND source_index IS NOT NULL AND source_index >= 0) OR "
        "(source_type = 'MINUTES_DRAFT' AND source_record_id IS NULL "
        "AND source_agent_run_id IS NOT NULL AND source_index IS NOT NULL AND source_index >= 0) OR "
        "(source_type = 'MANUAL' AND source_record_id IS NULL "
        "AND source_agent_run_id IS NULL AND source_index IS NULL)",
    )
    op.drop_constraint(
        "ck_visit_follow_ups_source_type_allowed",
        "visit_follow_ups",
        type_="check",
    )
    op.create_check_constraint(
        "ck_visit_follow_ups_source_type_allowed",
        "visit_follow_ups",
        "source_type IN ('RECORD','MINUTES_DRAFT','MANUAL')",
    )
