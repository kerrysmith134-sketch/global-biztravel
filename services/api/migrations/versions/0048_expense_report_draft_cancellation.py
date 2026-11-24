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
    op.create_foreign_key(
        "fk_expense_reports_cancelled_by_user_id_users",
        "expense_reports",
        "users",
        ["cancelled_by_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "ck_expense_reports_status_allowed",
        "expense_reports",
        type_="check",
    )
    op.create_check_constraint(
        "ck_expense_reports_status_allowed",
        "expense_reports",
        "status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED','CANCELLED')",
    )
    op.create_check_constraint(
        "ck_expense_reports_cancellation_metadata_consistent",
        "expense_reports",
        "(status = 'CANCELLED' AND cancelled_at IS NOT NULL "
        "AND cancellation_reason IS NOT NULL AND length(btrim(cancellation_reason)) > 0) "
        "OR (status <> 'CANCELLED' AND cancelled_at IS NULL "
        "AND cancelled_by_user_id IS NULL AND cancellation_reason IS NULL)",
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
    op.drop_constraint(
        "ck_expense_reports_cancellation_metadata_consistent",
        "expense_reports",
        type_="check",
    )
    op.drop_constraint(
        "ck_expense_reports_status_allowed",
        "expense_reports",
        type_="check",
    )
    op.create_check_constraint(
        "ck_expense_reports_status_allowed",
        "expense_reports",
        "status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED')",
    )
    op.drop_constraint(
        "fk_expense_reports_cancelled_by_user_id_users",
        "expense_reports",
        type_="foreignkey",
    )
    op.drop_column("expense_reports", "cancellation_reason")
    op.drop_column("expense_reports", "cancelled_by_user_id")
    op.drop_column("expense_reports", "cancelled_at")
