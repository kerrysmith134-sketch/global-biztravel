"""0033_market_question_dependencies — 显式研究依赖图与当前上下文引用约束。

Revision ID: 0033_market_question_dependencies
Revises: 0032_market_workspace_foundation
Create Date: 2026-11-22
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0033_market_question_dependencies"
down_revision = "0032_market_workspace_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The workspace and its initial context are inserted in one transaction.
    # Deferral permits that order while guaranteeing the pointer is valid at commit.
    op.execute(
        """
        ALTER TABLE market_workspaces
        ADD CONSTRAINT fk_market_workspaces_current_context_version
        FOREIGN KEY (organization_id, id, current_context_version)
        REFERENCES market_context_snapshots (organization_id, workspace_id, version)
        DEFERRABLE INITIALLY DEFERRED
        """
    )

    op.execute(
        """
        CREATE TABLE market_question_dependencies (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            question_id uuid NOT NULL,
            depends_on_question_id uuid NOT NULL,
            created_by_user_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_market_question_dependencies_not_self
                CHECK (question_id <> depends_on_question_id),
            CONSTRAINT uq_market_question_dependencies_edge
                UNIQUE (organization_id, workspace_id, question_id, depends_on_question_id),
            CONSTRAINT fk_market_question_dependencies_org
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_question_dependencies_workspace_same_org
                FOREIGN KEY (organization_id, workspace_id)
                REFERENCES market_workspaces (organization_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_question_dependencies_question_same_workspace
                FOREIGN KEY (organization_id, workspace_id, question_id)
                REFERENCES market_research_questions (organization_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT fk_market_question_dependencies_prerequisite_same_workspace
                FOREIGN KEY (organization_id, workspace_id, depends_on_question_id)
                REFERENCES market_research_questions (organization_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT fk_market_question_dependencies_creator
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_question_dependencies_organization_id "
        "ON market_question_dependencies (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_question_dependencies_org_workspace_question "
        "ON market_question_dependencies (organization_id, workspace_id, question_id)"
    )
    helpers.enable_org_rls("market_question_dependencies", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("market_question_dependencies")
    op.execute("DROP TABLE IF EXISTS market_question_dependencies")
    op.execute(
        "ALTER TABLE market_workspaces "
        "DROP CONSTRAINT IF EXISTS fk_market_workspaces_current_context_version"
    )
