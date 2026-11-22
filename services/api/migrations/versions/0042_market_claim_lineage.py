"""0042_market_claim_lineage — track immutable claim versions across answer versions."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0042_market_claim_lineage"
down_revision = "0041_market_question_evidence_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "market_claims",
        sa.Column("claim_lineage_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        """
        WITH lineage_map AS (
            SELECT organization_id, workspace_id, claim_key, gen_random_uuid() AS lineage_id
            FROM market_claims
            GROUP BY organization_id, workspace_id, claim_key
        )
        UPDATE market_claims AS claim
        SET claim_lineage_id = lineage_map.lineage_id
        FROM lineage_map
        WHERE claim.organization_id = lineage_map.organization_id
          AND claim.workspace_id = lineage_map.workspace_id
          AND claim.claim_key = lineage_map.claim_key
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY organization_id, workspace_id, claim_key
                       ORDER BY created_at ASC, version ASC, id ASC
                   ) AS lineage_version,
                   lag(id) OVER (
                       PARTITION BY organization_id, workspace_id, claim_key
                       ORDER BY created_at ASC, version ASC, id ASC
                   ) AS previous_id
            FROM market_claims
        )
        UPDATE market_claims AS claim
        SET version = ranked.lineage_version,
            supersedes_id = ranked.previous_id
        FROM ranked
        WHERE claim.id = ranked.id
        """
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY organization_id, workspace_id, claim_key
                       ORDER BY reviewed_at DESC NULLS LAST, created_at DESC, id DESC
                   ) AS accepted_rank
            FROM market_claims
            WHERE status = 'ACCEPTED'
        )
        UPDATE market_claims AS claim
        SET status = 'SUPERSEDED'
        FROM ranked
        WHERE claim.id = ranked.id
          AND ranked.accepted_rank > 1
        """
    )
    op.alter_column(
        "market_claims",
        "claim_lineage_id",
        nullable=False,
        server_default=sa.text("gen_random_uuid()"),
    )
    op.drop_constraint(
        "fk_market_claim_supersedes_same_answer", "market_claims", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_market_claim_supersedes_same_workspace",
        "market_claims",
        "market_claims",
        ["organization_id", "workspace_id", "supersedes_id"],
        ["organization_id", "workspace_id", "id"],
        ondelete="NO ACTION",
    )
    op.create_unique_constraint(
        "uq_market_claims_lineage_version",
        "market_claims",
        ["organization_id", "workspace_id", "claim_lineage_id", "version"],
    )
    op.drop_index("uq_market_claims_one_accepted_per_key", table_name="market_claims")
    op.create_index(
        "uq_market_claims_one_accepted_per_lineage",
        "market_claims",
        ["organization_id", "workspace_id", "claim_lineage_id"],
        unique=True,
        postgresql_where=sa.text("status = 'ACCEPTED'"),
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE market_claims AS child
        SET supersedes_id = NULL
        FROM market_claims AS parent
        WHERE child.supersedes_id = parent.id
          AND child.answer_id <> parent.answer_id
        """
    )
    op.drop_index("uq_market_claims_one_accepted_per_lineage", table_name="market_claims")
    op.create_index(
        "uq_market_claims_one_accepted_per_key",
        "market_claims",
        ["organization_id", "workspace_id", "answer_id", "claim_key"],
        unique=True,
        postgresql_where=sa.text("status = 'ACCEPTED'"),
    )
    op.drop_constraint("uq_market_claims_lineage_version", "market_claims", type_="unique")
    op.drop_constraint(
        "fk_market_claim_supersedes_same_workspace", "market_claims", type_="foreignkey"
    )
    op.create_foreign_key(
        "fk_market_claim_supersedes_same_answer",
        "market_claims",
        "market_claims",
        ["organization_id", "workspace_id", "answer_id", "supersedes_id"],
        ["organization_id", "workspace_id", "answer_id", "id"],
        ondelete="NO ACTION",
    )
    op.drop_column("market_claims", "claim_lineage_id")
