"""0037_market_question_answers — versioned answers and explicit support/contradiction evidence."""

from alembic import op

from migrations import helpers

revision = "0037_market_q_answers"
down_revision = "0036_market_plan_ev"
branch_labels = None
depends_on = None

TABLES = (
    "market_research_question_answers",
    "market_research_question_answer_evidence",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE market_research_question_answers (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            question_id uuid NOT NULL,
            version integer NOT NULL,
            answer_summary text NOT NULL,
            assumptions jsonb NOT NULL DEFAULT '[]',
            unknowns jsonb NOT NULL DEFAULT '[]',
            confidence_level text NOT NULL DEFAULT 'INSUFFICIENT',
            status text NOT NULL DEFAULT 'NEEDS_REVIEW',
            evidence_gap text,
            generated_by_run_id uuid,
            supersedes_id uuid,
            created_by_user_id uuid,
            reviewed_by_user_id uuid,
            reviewed_at timestamptz,
            review_note text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_question_answer_version
                UNIQUE (organization_id, question_id, version),
            CONSTRAINT uq_market_question_answer_org_workspace_id
                UNIQUE (organization_id, workspace_id, id),
            CONSTRAINT ck_market_research_question_answers_version_positive
                CHECK (version >= 1),
            CONSTRAINT ck_market_research_question_answers_status_allowed
                CHECK (status IN ('DRAFT','NEEDS_REVIEW','CONFLICTED','ACCEPTED','REJECTED','SUPERSEDED')),
            CONSTRAINT ck_market_research_question_answers_confidence_allowed
                CHECK (confidence_level IN ('HIGH','MEDIUM','LOW','INSUFFICIENT')),
            CONSTRAINT ck_market_research_question_answers_answer_not_blank
                CHECK (length(btrim(answer_summary)) > 0),
            CONSTRAINT ck_market_research_question_answers_accepted_has_reviewer
                CHECK (status <> 'ACCEPTED' OR (reviewed_by_user_id IS NOT NULL AND reviewed_at IS NOT NULL)),
            CONSTRAINT ck_market_research_question_answers_supersedes_not_self
                CHECK (supersedes_id IS NULL OR supersedes_id <> id),
            CONSTRAINT fk_market_question_answer_question_same_workspace
                FOREIGN KEY (organization_id, workspace_id, question_id)
                REFERENCES market_research_questions (organization_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT fk_market_question_answer_run_same_org
                FOREIGN KEY (organization_id, generated_by_run_id)
                REFERENCES agent_runs (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_question_answer_supersedes_same_workspace
                FOREIGN KEY (organization_id, workspace_id, supersedes_id)
                REFERENCES market_research_question_answers (organization_id, workspace_id, id)
                ON DELETE NO ACTION,
            CONSTRAINT fk_market_research_question_answers_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_research_question_answers_created_by_user_id_users
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT fk_market_research_question_answers_reviewed_by_user_id_users
                FOREIGN KEY (reviewed_by_user_id) REFERENCES users (id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_research_question_answers_organization_id "
        "ON market_research_question_answers (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_question_answers_org_question_version "
        "ON market_research_question_answers (organization_id, question_id, version)"
    )
    op.execute(
        "CREATE UNIQUE INDEX ix_market_question_answers_one_accepted "
        "ON market_research_question_answers (organization_id, workspace_id, question_id) "
        "WHERE status = 'ACCEPTED'"
    )

    op.execute(
        """
        CREATE TABLE market_research_question_answer_evidence (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            answer_id uuid NOT NULL,
            evidence_id uuid NOT NULL,
            relation_type text NOT NULL,
            rationale text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_question_answer_evidence_relation
                UNIQUE (organization_id, answer_id, evidence_id, relation_type),
            CONSTRAINT ck_market_research_question_answer_evidence_relation_type_allowed
                CHECK (relation_type IN ('SUPPORTS','CONTRADICTS','CONTEXT')),
            CONSTRAINT fk_market_question_answer_evidence_answer_same_workspace
                FOREIGN KEY (organization_id, workspace_id, answer_id)
                REFERENCES market_research_question_answers (organization_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT fk_market_question_answer_evidence_evidence_same_org
                FOREIGN KEY (organization_id, evidence_id)
                REFERENCES evidence (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_research_question_answer_evidence_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_research_question_answer_evidence_organization_id "
        "ON market_research_question_answer_evidence (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_question_answer_evidence_org_answer "
        "ON market_research_question_answer_evidence (organization_id, answer_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_question_answer_evidence_org_evidence "
        "ON market_research_question_answer_evidence (organization_id, evidence_id)"
    )
    for table in TABLES:
        helpers.enable_org_rls(table, force=True)


def downgrade() -> None:
    for table in reversed(TABLES):
        helpers.disable_org_rls(table)
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
