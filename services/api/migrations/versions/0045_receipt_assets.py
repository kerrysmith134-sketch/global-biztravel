"""0045_receipt_assets — preserve original receipts as private immutable assets."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from migrations import helpers

revision = "0045_receipt_assets"
down_revision = "0044_visit_follow_up_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "receipt_assets",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_provider", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("byte_length", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column(
            "storage_status", sa.Text(), nullable=False, server_default="AVAILABLE"
        ),
        sa.Column("uploaded_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "uploaded_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE",
            name="fk_receipt_assets_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "trip_id"], ["trips.organization_id", "trips.id"],
            ondelete="CASCADE", name="fk_receipt_assets_trip_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_user_id"], ["users.id"], ondelete="SET NULL",
            name="fk_receipt_assets_uploaded_by_user_id_users",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_receipt_assets_org_id"),
        sa.UniqueConstraint(
            "storage_provider", "storage_key", name="uq_receipt_assets_storage_object"
        ),
        sa.UniqueConstraint(
            "organization_id", "trip_id", "id", name="uq_receipt_assets_org_trip_id"
        ),
        sa.CheckConstraint("byte_length > 0", name="ck_receipt_assets_byte_length_positive"),
        sa.CheckConstraint("length(sha256) = 64", name="ck_receipt_assets_sha256_length"),
        sa.CheckConstraint(
            "storage_status IN ('AVAILABLE','QUARANTINED','DELETE_PENDING')",
            name="ck_receipt_assets_storage_status_allowed",
        ),
        sa.CheckConstraint(
            "storage_provider IN ('filesystem','s3')",
            name="ck_receipt_assets_storage_provider_allowed",
        ),
    )
    op.create_index(
        "ix_receipt_assets_organization_id", "receipt_assets", ["organization_id"]
    )
    op.create_index(
        "ix_receipt_assets_org_trip_created",
        "receipt_assets",
        ["organization_id", "trip_id", "created_at"],
    )
    op.create_index(
        "ix_receipt_assets_org_trip_sha256",
        "receipt_assets",
        ["organization_id", "trip_id", "sha256"],
    )
    helpers.enable_org_rls("receipt_assets", force=True)

    op.add_column(
        "trip_expenses",
        sa.Column("receipt_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_trip_expenses_receipt_asset_same_trip",
        "trip_expenses",
        "receipt_assets",
        ["organization_id", "trip_id", "receipt_asset_id"],
        ["organization_id", "trip_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_trip_expenses_receipt_asset_id", "trip_expenses", ["receipt_asset_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_trip_expenses_receipt_asset_id", table_name="trip_expenses")
    op.drop_constraint(
        "fk_trip_expenses_receipt_asset_same_trip", "trip_expenses", type_="foreignkey"
    )
    op.drop_column("trip_expenses", "receipt_asset_id")
    helpers.disable_org_rls("receipt_assets")
    op.drop_table("receipt_assets")
