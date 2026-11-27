"""0052_recovery_reassessment_events — audit evidence-backed recovery reassessments."""

from alembic import op

revision = "0052_recovery_reassessment_events"
down_revision = "0051_recovery_follow_ups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_visit_follow_up_events_event_type_allowed",
        "visit_follow_up_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_visit_follow_up_events_event_type_allowed",
        "visit_follow_up_events",
        "event_type IN ('ACCEPTED','DISMISSED','CREATED_MANUAL','STARTED','COMPLETED','REOPENED','UPDATED','REASSESSED')",
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM visit_follow_up_events
                WHERE event_type = 'REASSESSED'
            ) THEN
                RAISE EXCEPTION
                    'cannot downgrade while appointment recovery reassessment events exist';
            END IF;
        END $$;
        """
    )
    op.drop_constraint(
        "ck_visit_follow_up_events_event_type_allowed",
        "visit_follow_up_events",
        type_="check",
    )
    op.create_check_constraint(
        "ck_visit_follow_up_events_event_type_allowed",
        "visit_follow_up_events",
        "event_type IN ('ACCEPTED','DISMISSED','CREATED_MANUAL','STARTED','COMPLETED','REOPENED','UPDATED')",
    )
