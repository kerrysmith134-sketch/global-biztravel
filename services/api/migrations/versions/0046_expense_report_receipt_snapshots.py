"""0046_expense_report_receipt_snapshots — pin original receipt identity into report items."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0046_expense_report_receipt_snapshots"
down_revision = "0045_receipt_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_expense_reports_org_trip_id",
        "expense_reports",
        ["organization_id", "trip_id", "id"],
    )

    op.add_column(
        "expense_report_items",
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        UPDATE expense_report_items AS item
        SET trip_id = report.trip_id
        FROM expense_reports AS report
        WHERE report.id = item.report_id
          AND report.organization_id = item.organization_id
        """
    )
    missing_trip_scope = op.get_bind().execute(
        sa.text("SELECT count(*) FROM expense_report_items WHERE trip_id IS NULL")
    ).scalar_one()
    if missing_trip_scope:
        raise RuntimeError(
            f"Cannot add report trip scope: {missing_trip_scope} item(s) have no same-organization report"
        )
    op.alter_column("expense_report_items", "trip_id", nullable=False)

    op.drop_constraint(
        "fk_expense_report_items_report_id_expense_reports",
        "expense_report_items",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_expense_report_items_report_same_trip",
        "expense_report_items",
        "expense_reports",
        ["organization_id", "trip_id", "report_id"],
        ["organization_id", "trip_id", "id"],
        ondelete="CASCADE",
    )

    op.add_column(
        "expense_report_items",
        sa.Column("receipt_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "expense_report_items",
        sa.Column("receipt_sha256", sa.Text(), nullable=True),
    )
    op.add_column(
        "expense_report_items",
        sa.Column("receipt_original_filename", sa.Text(), nullable=True),
    )
    op.add_column(
        "expense_report_items",
        sa.Column("receipt_mime_type", sa.Text(), nullable=True),
    )
    op.add_column(
        "expense_report_items",
        sa.Column("receipt_byte_length", sa.Integer(), nullable=True),
    )

    op.create_foreign_key(
        "fk_expense_report_items_receipt_asset_same_trip",
        "expense_report_items",
        "receipt_assets",
        ["organization_id", "trip_id", "receipt_asset_id"],
        ["organization_id", "trip_id", "id"],
        ondelete="NO ACTION",
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_check_constraint(
        "ck_expense_report_items_receipt_snapshot_metadata_consistent",
        "expense_report_items",
        "("
        "receipt_asset_id IS NULL AND receipt_sha256 IS NULL "
        "AND receipt_original_filename IS NULL AND receipt_mime_type IS NULL "
        "AND receipt_byte_length IS NULL"
        ") OR ("
        "receipt_asset_id IS NOT NULL "
        "AND receipt_sha256 IS NOT NULL AND length(receipt_sha256) = 64 "
        "AND receipt_original_filename IS NOT NULL "
        "AND length(btrim(receipt_original_filename)) > 0 "
        "AND receipt_mime_type IS NOT NULL "
        "AND length(btrim(receipt_mime_type)) > 0 "
        "AND receipt_byte_length IS NOT NULL AND receipt_byte_length > 0"
        ")",
    )
    op.create_index(
        "ix_expense_report_items_receipt_asset_id",
        "expense_report_items",
        ["receipt_asset_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_expense_report_items_receipt_asset_id",
        table_name="expense_report_items",
    )
    op.drop_constraint(
        "ck_expense_report_items_receipt_snapshot_metadata_consistent",
        "expense_report_items",
        type_="check",
    )
    op.drop_constraint(
        "fk_expense_report_items_receipt_asset_same_trip",
        "expense_report_items",
        type_="foreignkey",
    )
    op.drop_column("expense_report_items", "receipt_byte_length")
    op.drop_column("expense_report_items", "receipt_mime_type")
    op.drop_column("expense_report_items", "receipt_original_filename")
    op.drop_column("expense_report_items", "receipt_sha256")
    op.drop_column("expense_report_items", "receipt_asset_id")

    op.drop_constraint(
        "fk_expense_report_items_report_same_trip",
        "expense_report_items",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_expense_report_items_report_id_expense_reports",
        "expense_report_items",
        "expense_reports",
        ["report_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.drop_column("expense_report_items", "trip_id")
    op.drop_constraint(
        "uq_expense_reports_org_trip_id",
        "expense_reports",
        type_="unique",
    )
