"""0035_market_opportunities — evidence-qualified opportunities and Workspace/Trip links."""

from alembic import op

from migrations import helpers

revision = "0035_market_opps"
down_revision = "0034_market_insights"
branch_labels = None
depends_on = None

TABLES = (
    "market_opportunities",
    "market_opportunity_assessments",
    "market_opportunity_evidence",
    "market_opportunity_entities",
    "market_workspace_trip_links",
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE trips ADD CONSTRAINT uq_trips_org_id UNIQUE (organization_id, id)"
    )
    op.execute(
        """
        CREATE TABLE market_opportunities (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            title text NOT NULL,
            description text,
            status text NOT NULL DEFAULT 'DISCOVERED',
            next_action text,
            qualification_reason text,
            disqualification_reason text,
            assessment_version integer NOT NULL DEFAULT 0,
            assessment_dimensions jsonb NOT NULL DEFAULT '{}',
            assessment_summary text,
            assessment_confidence text NOT NULL DEFAULT 'INSUFFICIENT',
            created_by_run_id uuid,
            owner_user_id uuid,
            created_by_user_id uuid,
            qualified_by_user_id uuid,
            qualified_at timestamptz,
            outcome_recorded_by_user_id uuid,
            outcome_recorded_at timestamptz,
            outcome_note text,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_opportunities_org_id UNIQUE (organization_id, id),
            CONSTRAINT uq_market_opportunities_org_workspace_id
                UNIQUE (organization_id, workspace_id, id),
            CONSTRAINT ck_market_opportunities_status_allowed
                CHECK (status IN (
                    'DISCOVERED','RESEARCHING','EVIDENCE_READY','QUALIFICATION_PENDING',
                    'QUALIFIED','ACTION_PLANNED','WON','LOST','DISQUALIFIED','ON_HOLD'
                )),
            CONSTRAINT ck_market_opportunities_assessment_version_non_negative
                CHECK (assessment_version >= 0),
            CONSTRAINT ck_market_opportunities_title_not_blank
                CHECK (length(btrim(title)) > 0),
            CONSTRAINT ck_market_opportunities_assessment_confidence_allowed
                CHECK (assessment_confidence IN ('HIGH','MEDIUM','LOW','INSUFFICIENT')),
            CONSTRAINT ck_market_opportunities_assessment_confidence_allowed
                CHECK (assessment_confidence IN ('HIGH','MEDIUM','LOW','INSUFFICIENT')),
            CONSTRAINT ck_market_opportunities_qualified_has_human_approval
                CHECK (
                    status NOT IN ('QUALIFIED','ACTION_PLANNED','WON','LOST') OR
                    (qualified_by_user_id IS NOT NULL AND qualified_at IS NOT NULL
                     AND length(btrim(qualification_reason)) > 0)
                ),
            CONSTRAINT ck_market_opportunities_terminal_outcome_has_actor_and_note
                CHECK (
                    status NOT IN ('WON','LOST') OR
                    (outcome_recorded_by_user_id IS NOT NULL
                     AND outcome_recorded_at IS NOT NULL
                     AND length(btrim(outcome_note)) > 0)
                ),
            CONSTRAINT ck_market_opportunities_disqualified_has_reason
                CHECK (status <> 'DISQUALIFIED' OR length(btrim(disqualification_reason)) > 0),
            CONSTRAINT fk_market_opportunity_workspace_same_org
                FOREIGN KEY (organization_id, workspace_id)
                REFERENCES market_workspaces (organization_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_opportunity_agent_run_same_org
                FOREIGN KEY (organization_id, created_by_run_id)
                REFERENCES agent_runs (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_opportunities_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_opportunities_owner_user_id_users
                FOREIGN KEY (owner_user_id) REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT fk_market_opportunities_created_by_user_id_users
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT fk_market_opportunities_qualified_by_user_id_users
                FOREIGN KEY (qualified_by_user_id) REFERENCES users (id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_opportunities_outcome_recorded_by_user_id_users
                FOREIGN KEY (outcome_recorded_by_user_id) REFERENCES users (id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_opportunities_organization_id "
        "ON market_opportunities (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_opportunities_org_workspace_status "
        "ON market_opportunities (organization_id, workspace_id, status)"
    )
    op.execute(
        "CREATE INDEX ix_market_opportunities_org_workspace_updated "
        "ON market_opportunities (organization_id, workspace_id, updated_at)"
    )

    op.execute(
        """
        CREATE TABLE market_opportunity_assessments (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            opportunity_id uuid NOT NULL,
            version integer NOT NULL,
            dimensions jsonb NOT NULL,
            summary text,
            confidence_level text NOT NULL,
            assessed_by_run_id uuid,
            assessed_by_user_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_opp_assessment_version
                UNIQUE (organization_id, opportunity_id, version),
            CONSTRAINT ck_market_opportunity_assessments_version_positive
                CHECK (version >= 1),
            CONSTRAINT ck_market_opportunity_assessments_confidence_allowed
                CHECK (confidence_level IN ('HIGH','MEDIUM','LOW','INSUFFICIENT')),
            CONSTRAINT fk_market_opp_assessment_opportunity_same_workspace
                FOREIGN KEY (organization_id, workspace_id, opportunity_id)
                REFERENCES market_opportunities (organization_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT fk_market_opp_assessment_agent_run_same_org
                FOREIGN KEY (organization_id, assessed_by_run_id)
                REFERENCES agent_runs (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_opportunity_assessments_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_opportunity_assessments_assessed_by_user_id_users
                FOREIGN KEY (assessed_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_opportunity_assessments_organization_id "
        "ON market_opportunity_assessments (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_opp_assessment_org_opp_created "
        "ON market_opportunity_assessments (organization_id, opportunity_id, created_at)"
    )

    op.execute(
        """
        CREATE TABLE market_opportunity_evidence (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            opportunity_id uuid NOT NULL,
            evidence_id uuid NOT NULL,
            relation_type text NOT NULL,
            rationale text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_opportunity_evidence_relation
                UNIQUE (organization_id, opportunity_id, evidence_id, relation_type),
            CONSTRAINT ck_market_opportunity_evidence_relation_type_allowed
                CHECK (relation_type IN ('SUPPORTS','CONTRADICTS','CONTEXT')),
            CONSTRAINT fk_market_opp_evidence_opportunity_same_workspace
                FOREIGN KEY (organization_id, workspace_id, opportunity_id)
                REFERENCES market_opportunities (organization_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT fk_market_opp_evidence_evidence_same_org
                FOREIGN KEY (organization_id, evidence_id)
                REFERENCES evidence (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_opportunity_evidence_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_opportunity_evidence_organization_id "
        "ON market_opportunity_evidence (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_opp_evidence_org_opp "
        "ON market_opportunity_evidence (organization_id, opportunity_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_opp_evidence_org_evidence "
        "ON market_opportunity_evidence (organization_id, evidence_id)"
    )

    op.execute(
        """
        CREATE TABLE market_opportunity_entities (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            opportunity_id uuid NOT NULL,
            entity_id uuid NOT NULL,
            relationship_note text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_opportunity_entity
                UNIQUE (organization_id, opportunity_id, entity_id),
            CONSTRAINT fk_market_opp_entity_opportunity_same_workspace
                FOREIGN KEY (organization_id, workspace_id, opportunity_id)
                REFERENCES market_opportunities (organization_id, workspace_id, id)
                ON DELETE CASCADE,
            CONSTRAINT fk_market_opp_entity_workspace_entity
                FOREIGN KEY (organization_id, workspace_id, entity_id)
                REFERENCES market_workspace_entities (organization_id, workspace_id, entity_id)
                ON DELETE RESTRICT,
            CONSTRAINT fk_market_opportunity_entities_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_opportunity_entities_organization_id "
        "ON market_opportunity_entities (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_opp_entities_org_workspace_entity "
        "ON market_opportunity_entities (organization_id, workspace_id, entity_id)"
    )

    op.execute(
        """
        CREATE TABLE market_workspace_trip_links (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            trip_id uuid NOT NULL,
            link_status text NOT NULL DEFAULT 'LINKED',
            link_note text,
            linked_by_user_id uuid,
            linked_at timestamptz NOT NULL DEFAULT now(),
            unlinked_by_user_id uuid,
            unlinked_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_workspace_trip_link
                UNIQUE (organization_id, workspace_id, trip_id),
            CONSTRAINT ck_market_workspace_trip_links_link_status_allowed
                CHECK (link_status IN ('LINKED','UNLINKED')),
            CONSTRAINT ck_market_workspace_trip_links_unlinked_has_actor_and_time
                CHECK (
                    link_status <> 'UNLINKED' OR
                    (unlinked_by_user_id IS NOT NULL AND unlinked_at IS NOT NULL)
                ),
            CONSTRAINT fk_market_workspace_trip_workspace_same_org
                FOREIGN KEY (organization_id, workspace_id)
                REFERENCES market_workspaces (organization_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_workspace_trip_trip_same_org
                FOREIGN KEY (organization_id, trip_id)
                REFERENCES trips (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_workspace_trip_links_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_workspace_trip_links_linked_by_user_id_users
                FOREIGN KEY (linked_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT fk_market_workspace_trip_links_unlinked_by_user_id_users
                FOREIGN KEY (unlinked_by_user_id) REFERENCES users (id) ON DELETE RESTRICT
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_workspace_trip_links_organization_id "
        "ON market_workspace_trip_links (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_workspace_trip_org_trip "
        "ON market_workspace_trip_links (organization_id, trip_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_workspace_trip_org_workspace_status "
        "ON market_workspace_trip_links (organization_id, workspace_id, link_status)"
    )

    for table in TABLES:
        helpers.enable_org_rls(table, force=True)
    helpers.ensure_updated_at_trigger("market_opportunities")
    helpers.ensure_updated_at_trigger("market_workspace_trip_links")


def downgrade() -> None:
    for table in reversed(TABLES):
        helpers.disable_org_rls(table)
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("ALTER TABLE trips DROP CONSTRAINT IF EXISTS uq_trips_org_id")
