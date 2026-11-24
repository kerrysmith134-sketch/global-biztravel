"""0048 — keep cancelled expense-report drafts as auditable snapshots."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0048_expense_report_draft_cancellation"
down_revision = "0047_receipt_ocr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "expense_reports",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "expense_reports",
        sa.Column("cancelled_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "expense_reports",
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
    )

    # The repository's naming convention prefixes explicitly supplied names.
    # Use exact SQL names here because 0022 created this legacy check via raw DDL.
    op.execute(
        """
        ALTER TABLE expense_reports
        ADD CONSTRAINT fk_expense_reports_cancelled_by_user_id_users
        FOREIGN KEY (cancelled_by_user_id)
        REFERENCES users (id) ON DELETE SET NULL
        """
    )
    op.execute(
        "ALTER TABLE expense_reports "
        "DROP CONSTRAINT ck_expense_reports_status_allowed"
    )
    op.execute(
        """
        ALTER TABLE expense_reports
        ADD CONSTRAINT ck_expense_reports_status_allowed
        CHECK (status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','CANCELLED'))
        """
    )
    op.execute(
        """
        ALTER TABLE expense_reports
        ADD CONSTRAINT ck_expense_reports_cancellation_metadata_consistent
        CHECK (
            (status = 'CANCELLED' AND cancelled_at IS NOT NULL
             AND cancellation_reason IS NOT NULL
             AND length(btrim(cancellation_reason)) > 0)
            OR
            (status <> 'CANCELLED' AND cancelled_at IS NULL
             AND cancelled_by_user_id IS NULL
             AND cancellation_reason IS NULL)
        )
        """
    )


def downgrade() -> None:
    # Do not silently reinterpret an explicit cancellation as a rejection.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM expense_reports WHERE status = 'CANCELLED'
            ) THEN
                RAISE EXCEPTION
                    'Cannot downgrade 0048: cancelled expense report snapshots exist';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        "ALTER TABLE expense_reports "
        "DROP CONSTRAINT ck_expense_reports_cancellation_metadata_consistent"
    )
    op.execute(
        "ALTER TABLE expense_reports "
        "DROP CONSTRAINT ck_expense_reports_status_allowed"
    )
    op.execute(
        """
        ALTER TABLE expense_reports
        ADD CONSTRAINT ck_expense_reports_status_allowed
        CHECK (status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED'))
        """
    )
    op.execute(
        "ALTER TABLE expense_reports "
        "DROP CONSTRAINT fk_expense_reports_cancelled_by_user_id_users"
    )
    op.drop_column("expense_reports", "cancellation_reason")
    op.drop_column("expense_reports", "cancelled_by_user_id")
    op.drop_column("expense_reports", "cancelled_at")
