"""0032_market_workspace_foundation — 市场工作空间、上下文版本与研究问题。

Revision ID: 0032_market_workspace_foundation
Revises: 0031_visa_catalog_overrides
Create Date: 2026-11-22
"""

from __future__ import annotations

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
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_market_workspaces_status_allowed
                CHECK (status IN ('DRAFT','ACTIVE','PAUSED','COMPLETED','ARCHIVED')),
            CONSTRAINT uq_market_workspaces_org_id UNIQUE (organization_id, id),
            CONSTRAINT fk_market_workspaces_org
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_workspaces_owner
                FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_workspaces_organization_id ON market_workspaces (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_workspaces_owner_user_id ON market_workspaces (owner_user_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_workspaces_org_status_updated "
        "ON market_workspaces (organization_id, status, updated_at)"
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
            unknowns jsonb NOT NULL DEFAULT '{}',
            provenance jsonb NOT NULL DEFAULT '{}',
            extraction_status text NOT NULL DEFAULT 'NOT_STARTED',
            extraction_prompt_id text,
            extraction_prompt_version text,
            is_current boolean NOT NULL DEFAULT false,
            created_by_user_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_market_context_snapshots_version_positive CHECK (version >= 1),
            CONSTRAINT ck_market_context_snapshots_extraction_status_allowed
                CHECK (extraction_status IN ('NOT_STARTED','PENDING','COMPLETED','FAILED')),
            CONSTRAINT uq_market_context_org_workspace_version
                UNIQUE (organization_id, workspace_id, version),
            CONSTRAINT uq_market_context_org_id UNIQUE (organization_id, id),
            CONSTRAINT fk_market_context_org
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_context_workspace_same_org
                FOREIGN KEY (organization_id, workspace_id)
                REFERENCES market_workspaces (organization_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_context_creator
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
        "ON market_context_snapshots (organization_id, workspace_id) WHERE is_current"
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
            acceptance_criteria jsonb NOT NULL DEFAULT '{}',
            answer_summary text,
            blocked_reason text,
            created_by_user_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_market_research_questions_priority_range
                CHECK (priority BETWEEN 1 AND 5),
            CONSTRAINT ck_market_research_questions_status_allowed
                CHECK (status IN ('OPEN','IN_PROGRESS','ANSWERED','BLOCKED','CANCELLED')),
            CONSTRAINT ck_market_research_questions_source_allowed
                CHECK (source IN ('USER','AGENT')),
            CONSTRAINT uq_market_questions_org_workspace_id
                UNIQUE (organization_id, workspace_id, id),
            CONSTRAINT fk_market_questions_org
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_questions_workspace_same_org
                FOREIGN KEY (organization_id, workspace_id)
                REFERENCES market_workspaces (organization_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_questions_parent_same_workspace
                FOREIGN KEY (organization_id, workspace_id, parent_question_id)
                REFERENCES market_research_questions (organization_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT fk_market_questions_creator
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_research_questions_organization_id "
        "ON market_research_questions (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_questions_org_workspace_priority "
        "ON market_research_questions (organization_id, workspace_id, priority, created_at)"
    )

    op.execute(
        """
        CREATE TABLE market_activity_events (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            actor_type text NOT NULL,
            actor_user_id uuid,
            aggregate_type text NOT NULL,
            aggregate_id uuid NOT NULL,
            event_type text NOT NULL,
            payload jsonb NOT NULL DEFAULT '{}',
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_market_activity_events_actor_type_allowed
                CHECK (actor_type IN ('USER','SYSTEM')),
            CONSTRAINT fk_market_activity_org
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_activity_workspace_same_org
                FOREIGN KEY (organization_id, workspace_id)
                REFERENCES market_workspaces (organization_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_activity_actor
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

    helpers.ensure_updated_at_trigger("market_workspaces", "market_research_questions")
    for table in TABLES:
        helpers.enable_org_rls(table, force=True)

    # Activity events are append-only for the application role. A privileged maintenance
    # session may still clean up test data or perform an audited repair.
    if helpers.role_exists("gbt_app"):
        op.execute("REVOKE UPDATE, DELETE, TRUNCATE ON market_activity_events FROM gbt_app")


def downgrade() -> None:
    # Downgrade removes only the new H1.2 objects; existing Trip/Customer/Evidence data is untouched.
    for table in reversed(TABLES):
        helpers.disable_org_rls(table)
    op.execute("DROP TABLE IF EXISTS market_activity_events")
    op.execute("DROP TABLE IF EXISTS market_research_questions")
    op.execute("DROP TABLE IF EXISTS market_context_snapshots")
    op.execute("DROP TABLE IF EXISTS market_workspaces")
