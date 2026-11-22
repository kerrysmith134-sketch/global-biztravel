"""0047_receipt_ocr — stored-asset OCR provenance and append-only human review."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from migrations import helpers

revision = "0047_receipt_ocr"
down_revision = "0046_report_receipt_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "receipt_ocr_runs",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receipt_asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="RUNNING"),
        sa.Column("candidates", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "started_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE",
            name="fk_receipt_ocr_runs_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "receipt_asset_id"],
            ["receipt_assets.organization_id", "receipt_assets.id"],
            ondelete="CASCADE",
            name="fk_receipt_ocr_runs_asset_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "agent_run_id"],
            ["agent_runs.organization_id", "agent_runs.id"],
            ondelete="RESTRICT",
            name="fk_receipt_ocr_runs_agent_run_same_org",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_receipt_ocr_runs_org_id"),
        sa.UniqueConstraint("organization_id", "agent_run_id", name="uq_receipt_ocr_runs_agent"),
        sa.CheckConstraint(
            "status IN ('RUNNING','SUCCEEDED','FAILED')",
            name="ck_receipt_ocr_runs_status_allowed",
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name="ck_receipt_ocr_runs_content_sha256_length",
        ),
        sa.CheckConstraint(
            "length(btrim(engine)) > 0",
            name="ck_receipt_ocr_runs_engine_not_blank",
        ),
        sa.CheckConstraint(
            "length(btrim(engine_version)) > 0",
            name="ck_receipt_ocr_runs_engine_version_not_blank",
        ),
    )
    op.create_index(
        "ix_receipt_ocr_runs_organization_id",
        "receipt_ocr_runs",
        ["organization_id"],
    )
    op.create_index(
        "ix_receipt_ocr_runs_org_asset_created",
        "receipt_ocr_runs",
        ["organization_id", "receipt_asset_id", "created_at"],
    )
    helpers.enable_org_rls("receipt_ocr_runs", force=True)

    op.create_table(
        "receipt_ocr_reviews",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("receipt_ocr_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("confirmed_fields", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("reviewed_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "reviewed_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="CASCADE",
            name="fk_receipt_ocr_reviews_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "receipt_ocr_run_id"],
            ["receipt_ocr_runs.organization_id", "receipt_ocr_runs.id"],
            ondelete="CASCADE",
            name="fk_receipt_ocr_reviews_run_same_org",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL",
            name="fk_receipt_ocr_reviews_reviewed_by_user_id_users",
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_receipt_ocr_reviews_org_id"),
        sa.UniqueConstraint(
            "organization_id", "receipt_ocr_run_id", name="uq_receipt_ocr_reviews_run"
        ),
        sa.CheckConstraint(
            "decision IN ('CONFIRMED','REJECTED')",
            name="ck_receipt_ocr_reviews_decision_allowed",
        ),
        sa.CheckConstraint(
            "(decision = 'CONFIRMED' AND confirmed_fields <> '{}'::jsonb) OR decision = 'REJECTED'",
            name="ck_receipt_ocr_reviews_decision_fields_consistent",
        ),
    )
    op.create_index(
        "ix_receipt_ocr_reviews_organization_id",
        "receipt_ocr_reviews",
        ["organization_id"],
    )
    op.create_index(
        "ix_receipt_ocr_reviews_org_created",
        "receipt_ocr_reviews",
        ["organization_id", "created_at"],
    )
    helpers.enable_org_rls("receipt_ocr_reviews", force=True)

    # OCR reviews are immutable decisions. Runtime may read and append them, but
    # it cannot rewrite or delete a prior human decision.
    if helpers.role_exists("gbt_app"):
        op.execute(
            "REVOKE UPDATE, DELETE, TRUNCATE ON receipt_ocr_reviews FROM gbt_app"
        )


def downgrade() -> None:
    helpers.disable_org_rls("receipt_ocr_reviews")
    helpers.disable_org_rls("receipt_ocr_runs")
    op.drop_table("receipt_ocr_reviews")
    op.drop_table("receipt_ocr_runs")
