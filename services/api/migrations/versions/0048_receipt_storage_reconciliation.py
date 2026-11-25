"""0048_receipt_storage_reconciliation — audit and age-gated orphan cleanup."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from migrations import helpers

revision = "0048_receipt_orphans"
down_revision = "0047_receipt_ocr"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "receipt_storage_orphans",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("storage_provider", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("object_size", sa.BigInteger(), nullable=False),
        sa.Column("object_last_modified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="DISCOVERED"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delete_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
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
            name="fk_receipt_storage_orphans_organization_id_organizations",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "storage_provider",
            "storage_key",
            name="uq_receipt_storage_orphans_org_provider_key",
        ),
        sa.CheckConstraint(
            "storage_provider IN ('filesystem','s3')",
            name="ck_receipt_storage_orphans_storage_provider_allowed",
        ),
        sa.CheckConstraint(
            "status IN ('DISCOVERED','DELETE_PENDING','DELETE_FAILED','DELETED','MISSING','RESOLVED')",
            name="ck_receipt_storage_orphans_status_allowed",
        ),
        sa.CheckConstraint("object_size >= 0", name="ck_receipt_storage_orphans_object_size_non_negative"),
        sa.CheckConstraint(
            "delete_attempts >= 0",
            name="ck_receipt_storage_orphans_delete_attempts_non_negative",
        ),
    )
    op.create_index(
        "ix_receipt_storage_orphans_organization_id",
        "receipt_storage_orphans",
        ["organization_id"],
    )
    op.create_index(
        "ix_receipt_storage_orphans_org_status_first_seen",
        "receipt_storage_orphans",
        ["organization_id", "status", "first_seen_at"],
    )
    helpers.enable_org_rls("receipt_storage_orphans", force=True)
    helpers.ensure_updated_at_trigger("receipt_storage_orphans")


def downgrade() -> None:
    helpers.disable_org_rls("receipt_storage_orphans")
    op.drop_table("receipt_storage_orphans")
