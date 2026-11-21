"""0032_market_workspace_foundation — 市场 Workspace、上下文版本与研究问题。

H1.2 首个后端纵向切片。市场研究可独立存在，不要求先创建 Trip。
"""

from alembic import op

from migrations import helpers

revision = "0032_market_workspace_foundation"
down_revision = "0031_visa_catalog_overrides"
branch_labels = None
depends_on = None

TABLES = (
    "market_workspaces",
    "market_context_snapshots",
    "market_research_questions",
    "market_question_dependencies",
    "market_activity_events",
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE market_workspaces (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            name text NOT NULL,
            objective text,
            status text NOT NULL DEFAULT 'DRAFT',
            owner_user_id uuid,
            current_context_version integer,
            settings jsonb NOT NULL DEFAULT '{}',
            created_by_user_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_workspaces_org_id UNIQUE (organization_id, id),
            CONSTRAINT ck_market_workspaces_status_allowed
                CHECK (status IN ('DRAFT','ACTIVE','PAUSED','COMPLETED','ARCHIVED')),
            CONSTRAINT fk_market_workspaces_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_workspaces_owner_user_id_users
                FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT fk_market_workspaces_created_by_user_id_users
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_workspaces_organization_id ON market_workspaces (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_workspaces_org_status_created "
        "ON market_workspaces (organization_id, status, created_at)"
    )

    op.execute(
        """
        CREATE TABLE market_context_snapshots (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            version integer NOT NULL,
            raw_input text NOT NULL,
            extracted_context jsonb NOT NULL DEFAULT '{}',
            user_overrides jsonb NOT NULL DEFAULT '{}',
            unknowns jsonb NOT NULL DEFAULT '[]',
            provenance jsonb NOT NULL DEFAULT '{}',
            extraction_status text NOT NULL DEFAULT 'NOT_STARTED',
            extraction_prompt_id text,
            extraction_prompt_version text,
            is_current boolean NOT NULL DEFAULT false,
            created_by_user_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_context_workspace_version
                UNIQUE (organization_id, workspace_id, version),
            CONSTRAINT ck_market_context_snapshots_version_positive CHECK (version >= 1),
            CONSTRAINT ck_market_context_snapshots_extraction_status_allowed
                CHECK (extraction_status IN ('NOT_STARTED','PENDING','COMPLETED','FAILED')),
            CONSTRAINT fk_market_context_workspace_same_org
                FOREIGN KEY (organization_id, workspace_id)
                REFERENCES market_workspaces (organization_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_context_snapshots_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_context_snapshots_created_by_user_id_users
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_context_snapshots_organization_id "
        "ON market_context_snapshots (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_context_org_workspace_created "
        "ON market_context_snapshots (organization_id, workspace_id, created_at)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_market_context_current "
        "ON market_context_snapshots (organization_id, workspace_id) WHERE is_current = true"
    )

    op.execute(
        """
        CREATE TABLE market_research_questions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            parent_question_id uuid,
            question text NOT NULL,
            rationale text,
            source text NOT NULL DEFAULT 'USER',
            priority smallint NOT NULL DEFAULT 3,
            status text NOT NULL DEFAULT 'OPEN',
            acceptance_criteria jsonb NOT NULL DEFAULT '[]',
            answer_summary text,
            blocked_reason text,
            created_by_user_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_questions_org_workspace_id
                UNIQUE (organization_id, workspace_id, id),
            CONSTRAINT ck_market_research_questions_priority_range
                CHECK (priority >= 1 AND priority <= 5),
            CONSTRAINT ck_market_research_questions_source_allowed
                CHECK (source IN ('USER','AGENT')),
            CONSTRAINT ck_market_research_questions_status_allowed
                CHECK (status IN ('OPEN','IN_PROGRESS','ANSWERED','BLOCKED','CANCELLED')),
            CONSTRAINT ck_market_research_questions_parent_not_self
                CHECK (parent_question_id IS NULL OR parent_question_id <> id),
            CONSTRAINT fk_market_question_workspace_same_org
                FOREIGN KEY (organization_id, workspace_id)
                REFERENCES market_workspaces (organization_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_question_parent_same_workspace
                FOREIGN KEY (organization_id, workspace_id, parent_question_id)
                REFERENCES market_research_questions (organization_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT fk_market_research_questions_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_research_questions_created_by_user_id_users
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_research_questions_organization_id "
        "ON market_research_questions (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_questions_org_workspace_status_priority "
        "ON market_research_questions (organization_id, workspace_id, status, priority)"
    )

    op.execute(
        """
        CREATE TABLE market_question_dependencies (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            question_id uuid NOT NULL,
            depends_on_question_id uuid NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_question_dependency
                UNIQUE (organization_id, question_id, depends_on_question_id),
            CONSTRAINT ck_market_question_dependencies_not_self_dependency
                CHECK (question_id <> depends_on_question_id),
            CONSTRAINT fk_market_question_dependency_question
                FOREIGN KEY (organization_id, workspace_id, question_id)
                REFERENCES market_research_questions (organization_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT fk_market_question_dependency_target
                FOREIGN KEY (organization_id, workspace_id, depends_on_question_id)
                REFERENCES market_research_questions (organization_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT fk_market_question_dependencies_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_question_dependencies_organization_id "
        "ON market_question_dependencies (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_question_dependencies_workspace "
        "ON market_question_dependencies (organization_id, workspace_id)"
    )

    op.execute(
        """
        CREATE TABLE market_activity_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            actor_type text NOT NULL DEFAULT 'USER',
            actor_user_id uuid,
            aggregate_type text NOT NULL,
            aggregate_id uuid NOT NULL,
            event_type text NOT NULL,
            payload jsonb NOT NULL DEFAULT '{}',
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_market_activity_events_actor_type_allowed
                CHECK (actor_type IN ('USER','SYSTEM')),
            CONSTRAINT fk_market_activity_workspace_same_org
                FOREIGN KEY (organization_id, workspace_id)
                REFERENCES market_workspaces (organization_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_activity_events_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_activity_events_actor_user_id_users
                FOREIGN KEY (actor_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_activity_events_organization_id "
        "ON market_activity_events (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_activity_org_workspace_created "
        "ON market_activity_events (organization_id, workspace_id, created_at)"
    )
    op.execute(
        "CREATE INDEX ix_market_activity_org_aggregate "
        "ON market_activity_events (organization_id, aggregate_type, aggregate_id)"
    )

    for table in TABLES:
        helpers.enable_org_rls(table, force=True)
    helpers.ensure_updated_at_trigger("market_workspaces", "market_research_questions")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS market_activity_events")
    op.execute("DROP TABLE IF EXISTS market_question_dependencies")
    op.execute("DROP TABLE IF EXISTS market_research_questions")
    op.execute("DROP TABLE IF EXISTS market_context_snapshots")
    op.execute("DROP TABLE IF EXISTS market_workspaces")
