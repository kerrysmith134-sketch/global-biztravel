"""0036_market_research_plan_evidence — connect ResearchPlans to shared versioned Workspace Evidence."""

from alembic import op

from migrations import helpers

revision = "0036_market_plan_ev"
down_revision = "0035_market_opps"
branch_labels = None
depends_on = None

TABLES = ("market_research_plan_evidence",)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE market_research_plan_evidence (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            research_plan_id uuid NOT NULL,
            evidence_id uuid NOT NULL,
            search_query text NOT NULL,
            query_sha256 text NOT NULL,
            collected_by_run_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_research_plan_evidence_query
                UNIQUE (
                    organization_id, workspace_id, research_plan_id,
                    evidence_id, query_sha256
                ),
            CONSTRAINT ck_market_research_plan_evidence_search_query_not_blank
                CHECK (length(btrim(search_query)) > 0),
            CONSTRAINT ck_market_research_plan_evidence_query_sha256_length
                CHECK (length(query_sha256) = 64),
            CONSTRAINT fk_market_research_plan_evidence_plan_workspace
                FOREIGN KEY (organization_id, workspace_id, research_plan_id)
                REFERENCES market_workspace_workflows
                    (organization_id, workspace_id, workflow_id)
                ON DELETE CASCADE,
            CONSTRAINT fk_market_research_plan_evidence_evidence_same_org
                FOREIGN KEY (organization_id, evidence_id)
                REFERENCES evidence (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_research_plan_evidence_run_same_org
                FOREIGN KEY (organization_id, collected_by_run_id)
                REFERENCES agent_runs (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_research_plan_evidence_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_research_plan_evidence_organization_id "
        "ON market_research_plan_evidence (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_research_plan_evidence_org_plan "
        "ON market_research_plan_evidence "
        "(organization_id, workspace_id, research_plan_id, created_at)"
    )
    op.execute(
        "CREATE INDEX ix_market_research_plan_evidence_org_evidence "
        "ON market_research_plan_evidence (organization_id, evidence_id)"
    )
    helpers.enable_org_rls("market_research_plan_evidence", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("market_research_plan_evidence")
    op.execute("DROP TABLE IF EXISTS market_research_plan_evidence")
