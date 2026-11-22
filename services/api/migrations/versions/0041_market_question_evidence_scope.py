"""0041_market_question_evidence_scope — attach ResearchPlan evidence to one question."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0041_market_question_evidence_scope"
down_revision = "0040_market_research_graph"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "market_research_plan_evidence",
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_market_research_plan_evidence_question_same_workspace",
        "market_research_plan_evidence",
        "market_research_questions",
        ["organization_id", "workspace_id", "question_id"],
        ["organization_id", "workspace_id", "id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_market_research_plan_evidence_org_question",
        "market_research_plan_evidence",
        ["organization_id", "workspace_id", "question_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_market_research_plan_evidence_org_question",
        table_name="market_research_plan_evidence",
    )
    op.drop_constraint(
        "fk_market_research_plan_evidence_question_same_workspace",
        "market_research_plan_evidence",
        type_="foreignkey",
    )
    op.drop_column("market_research_plan_evidence", "question_id")
