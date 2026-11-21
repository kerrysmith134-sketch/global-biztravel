"""0034_market_insights_evidence_versions — versioned Evidence and reviewed MarketInsight."""

from alembic import op

from migrations import helpers

revision = "0034_market_insights_evidence_versions"
down_revision = "0033_market_entities"
branch_labels = None
depends_on = None

TABLES = ("market_insights", "market_insight_evidence")


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_runs ADD CONSTRAINT uq_agent_runs_org_id UNIQUE (organization_id, id)"
    )
    op.execute(
        """
        ALTER TABLE evidence
            ADD COLUMN published_at timestamptz,
            ADD COLUMN canonical_ref text,
            ADD COLUMN content_hash text,
            ADD COLUMN observation text,
            ADD COLUMN verification_status text,
            ADD COLUMN quality jsonb,
            ADD CONSTRAINT ck_evidence_verification_status
                CHECK (
                    verification_status IS NULL OR
                    verification_status IN (
                        'UNVERIFIED','PARTIALLY_VERIFIED','VERIFIED','DISPUTED','REJECTED'
                    )
                )
        """
    )
    op.execute(
        "CREATE INDEX ix_evidence_org_canonical_ref ON evidence (organization_id, canonical_ref)"
    )
    op.execute(
        """
        CREATE TABLE market_insights (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            version integer NOT NULL,
            insight_key text NOT NULL,
            insight_type text NOT NULL,
            statement text NOT NULL,
            assumptions jsonb NOT NULL DEFAULT '[]',
            unknowns jsonb NOT NULL DEFAULT '[]',
            implications jsonb NOT NULL DEFAULT '[]',
            confidence_level text NOT NULL DEFAULT 'INSUFFICIENT',
            status text NOT NULL DEFAULT 'NEEDS_REVIEW',
            generated_by_run_id uuid,
            supersedes_id uuid,
            insufficient_evidence_reason text,
            created_by_user_id uuid,
            reviewed_by_user_id uuid,
            reviewed_at timestamptz,
            review_note text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_insights_org_id UNIQUE (organization_id, id),
            CONSTRAINT uq_market_insights_org_workspace_id UNIQUE (organization_id, workspace_id, id),
            CONSTRAINT uq_market_insights_workspace_key_version
                UNIQUE (organization_id, workspace_id, insight_key, version),
            CONSTRAINT ck_market_insights_version_positive CHECK (version >= 1),
            CONSTRAINT ck_market_insights_status_allowed
                CHECK (status IN ('DRAFT','NEEDS_REVIEW','ACCEPTED','REJECTED','CONFLICTED','SUPERSEDED')),
            CONSTRAINT ck_market_insights_confidence_allowed
                CHECK (confidence_level IN ('HIGH','MEDIUM','LOW','INSUFFICIENT')),
            CONSTRAINT ck_market_insights_statement_not_blank CHECK (length(btrim(statement)) > 0),
            CONSTRAINT ck_market_insights_accepted_has_reviewer
                CHECK (status <> 'ACCEPTED' OR (reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL)),
            CONSTRAINT ck_market_insights_supersedes_not_self
                CHECK (supersedes_id IS NULL OR supersedes_id <> id),
            CONSTRAINT fk_market_insight_workspace_same_org
                FOREIGN KEY (organization_id, workspace_id)
                REFERENCES market_workspaces (organization_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_insight_agent_run_same_org
                FOREIGN KEY (organization_id, generated_by_run_id)
                REFERENCES agent_runs (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_insight_supersedes_same_workspace
                FOREIGN KEY (organization_id, workspace_id, supersedes_id)
                REFERENCES market_insights (organization_id, workspace_id, id) ON DELETE NO ACTION,
            CONSTRAINT fk_market_insights_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_insights_created_by_user_id_users
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT fk_market_insights_reviewed_by_user_id_users
                FOREIGN KEY (reviewed_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_insights_organization_id ON market_insights (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_insights_org_workspace_created "
        "ON market_insights (organization_id, workspace_id, created_at)"
    )
    op.execute(
        "CREATE INDEX ix_market_insights_org_workspace_key "
        "ON market_insights (organization_id, workspace_id, insight_key, version)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_market_insights_one_accepted_per_key "
        "ON market_insights (organization_id, workspace_id, insight_key) "
        "WHERE status = 'ACCEPTED'"
    )
    op.execute(
        """
        CREATE TABLE market_insight_evidence (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            insight_id uuid NOT NULL,
            evidence_id uuid NOT NULL,
            relation_type text NOT NULL,
            rationale text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_insight_evidence_relation
                UNIQUE (organization_id, insight_id, evidence_id, relation_type),
            CONSTRAINT ck_market_insight_evidence_relation_type_allowed
                CHECK (relation_type IN ('SUPPORTS','CONTRADICTS','CONTEXT')),
            CONSTRAINT fk_market_insight_evidence_insight_same_org
                FOREIGN KEY (organization_id, insight_id)
                REFERENCES market_insights (organization_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_insight_evidence_evidence_same_org
                FOREIGN KEY (organization_id, evidence_id)
                REFERENCES evidence (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_insight_evidence_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_insight_evidence_organization_id "
        "ON market_insight_evidence (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_insight_evidence_org_insight "
        "ON market_insight_evidence (organization_id, insight_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_insight_evidence_org_evidence "
        "ON market_insight_evidence (organization_id, evidence_id)"
    )
    for table in TABLES:
        helpers.enable_org_rls(table, force=True)
    helpers.ensure_updated_at_trigger("market_insights")


def downgrade() -> None:
    for table in reversed(TABLES):
        helpers.disable_org_rls(table)
    op.execute("DROP TABLE IF EXISTS market_insight_evidence")
    op.execute("DROP TABLE IF EXISTS market_insights")
    op.execute("ALTER TABLE evidence DROP CONSTRAINT IF EXISTS ck_evidence_verification_status")
    op.execute("DROP INDEX IF EXISTS ix_evidence_org_canonical_ref")
    op.execute(
        """
        ALTER TABLE evidence
            DROP COLUMN IF EXISTS quality,
            DROP COLUMN IF EXISTS verification_status,
            DROP COLUMN IF EXISTS observation,
            DROP COLUMN IF EXISTS content_hash,
            DROP COLUMN IF EXISTS canonical_ref,
            DROP COLUMN IF EXISTS published_at
        """
    )
    op.execute("ALTER TABLE agent_runs DROP CONSTRAINT IF EXISTS uq_agent_runs_org_id")
