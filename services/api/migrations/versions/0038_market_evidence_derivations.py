"""0038_market_evidence_derivations — preserve immutable source extraction lineage."""

from alembic import op

from migrations import helpers

revision = "0038_market_evidence_derivations"
down_revision = "0037_market_q_answers"
branch_labels = None
depends_on = None

TABLE = "market_evidence_derivations"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE market_evidence_derivations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            source_evidence_id uuid NOT NULL,
            derived_evidence_id uuid NOT NULL,
            workflow_id uuid NOT NULL,
            agent_run_id uuid NOT NULL,
            derivation_type text NOT NULL DEFAULT 'WEB_PAGE_TEXT_EXTRACTION',
            extractor_version text NOT NULL,
            source_url text NOT NULL,
            content_hash text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_market_evidence_derivation_workspace
                FOREIGN KEY (organization_id, workspace_id)
                REFERENCES market_workspaces (organization_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_evidence_derivation_workflow
                FOREIGN KEY (organization_id, workspace_id, workflow_id)
                REFERENCES market_workspace_workflows (organization_id, workspace_id, workflow_id)
                ON DELETE CASCADE,
            CONSTRAINT fk_market_evidence_derivation_source
                FOREIGN KEY (organization_id, source_evidence_id)
                REFERENCES evidence (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_evidence_derivation_result
                FOREIGN KEY (organization_id, derived_evidence_id)
                REFERENCES evidence (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_evidence_derivation_run
                FOREIGN KEY (organization_id, agent_run_id)
                REFERENCES agent_runs (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT uq_market_evidence_derivation
                UNIQUE (organization_id, workspace_id, source_evidence_id, derived_evidence_id, derivation_type),
            CONSTRAINT ck_market_evidence_derivation_source_not_result
                CHECK (source_evidence_id <> derived_evidence_id),
            CONSTRAINT ck_market_evidence_derivation_derivation_type_allowed
                CHECK (derivation_type = 'WEB_PAGE_TEXT_EXTRACTION'),
            CONSTRAINT ck_market_evidence_derivation_extractor_version_not_blank
                CHECK (length(btrim(extractor_version)) > 0),
            CONSTRAINT ck_market_evidence_derivation_content_hash_length
                CHECK (length(content_hash) = 64)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_evidence_derivation_org_workspace_source "
        "ON market_evidence_derivations (organization_id, workspace_id, source_evidence_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_evidence_derivation_org_derived "
        "ON market_evidence_derivations (organization_id, derived_evidence_id)"
    )
    helpers.enable_org_rls(TABLE, force=True)
    # The provenance graph is immutable for the application role.
    if helpers.role_exists("gbt_app"):
        op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {TABLE} FROM gbt_app")


def downgrade() -> None:
    helpers.disable_org_rls(TABLE)
    op.execute(f"DROP TABLE IF EXISTS {TABLE}")
