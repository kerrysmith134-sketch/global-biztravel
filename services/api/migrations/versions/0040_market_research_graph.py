"""0040_market_research_graph — persist planner provenance and expected evidence."""

from alembic import op

revision = "0040_market_research_graph"
down_revision = "0039_market_claim_verification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE market_research_questions
            ADD COLUMN expected_evidence jsonb NOT NULL DEFAULT '[]',
            ADD COLUMN generated_by_run_id uuid,
            ADD COLUMN planner_question_key text,
            ADD CONSTRAINT ck_market_research_questions_planner_provenance_pair
                CHECK ((generated_by_run_id IS NULL) = (planner_question_key IS NULL)),
            ADD CONSTRAINT fk_market_question_planner_run_same_org
                FOREIGN KEY (organization_id, generated_by_run_id)
                REFERENCES agent_runs (organization_id, id) ON DELETE RESTRICT,
            ADD CONSTRAINT uq_market_question_planner_node
                UNIQUE (organization_id, workspace_id, generated_by_run_id, planner_question_key)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE market_research_questions
            DROP CONSTRAINT IF EXISTS uq_market_question_planner_node,
            DROP CONSTRAINT IF EXISTS fk_market_question_planner_run_same_org,
            DROP CONSTRAINT IF EXISTS ck_market_questions_planner_provenance_pair,
            DROP COLUMN IF EXISTS planner_question_key,
            DROP COLUMN IF EXISTS generated_by_run_id,
            DROP COLUMN IF EXISTS expected_evidence
        """
    )
