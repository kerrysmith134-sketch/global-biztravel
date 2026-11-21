"""0033_market_entities — 市场候选实体、别名、Workspace 关联及 Customer 匹配建议。

实体发现与正式 Customer 主数据分离；只有显式确认才建立 USER_CONFIRMED link。
"""

from alembic import op

from migrations import helpers

revision = "0033_market_entities"
down_revision = "0032_market_workspace_foundation"
branch_labels = None
depends_on = None

TABLES = (
    "market_entities",
    "market_entity_aliases",
    "market_workspace_entities",
    "market_entity_customer_links",
)


def upgrade() -> None:
    # Composite keys let the database enforce tenant equality on cross-domain references.
    op.execute(
        "ALTER TABLE customers ADD CONSTRAINT uq_customers_org_id UNIQUE (organization_id, id)"
    )
    op.execute(
        "ALTER TABLE evidence ADD CONSTRAINT uq_evidence_org_id UNIQUE (organization_id, id)"
    )

    op.execute(
        """
        CREATE TABLE market_entities (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            entity_kind text NOT NULL,
            display_name text NOT NULL,
            normalized_name text NOT NULL,
            description text,
            canonical_domain text,
            country_code char(2),
            attributes jsonb NOT NULL DEFAULT '{}',
            status text NOT NULL DEFAULT 'CANDIDATE',
            created_by_user_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_entities_org_id UNIQUE (organization_id, id),
            CONSTRAINT ck_market_entities_status_allowed
                CHECK (status IN ('CANDIDATE','REVIEWED','VERIFIED','REJECTED','MERGED')),
            CONSTRAINT fk_market_entities_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_entities_created_by_user_id_users
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_entities_organization_id ON market_entities (organization_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_market_entities_org_domain "
        "ON market_entities (organization_id, canonical_domain) "
        "WHERE canonical_domain IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_market_entities_org_kind_name "
        "ON market_entities (organization_id, entity_kind, normalized_name)"
    )

    op.execute(
        """
        CREATE TABLE market_entity_aliases (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            entity_id uuid NOT NULL,
            alias_type text NOT NULL,
            raw_value text NOT NULL,
            normalized_value text NOT NULL,
            source_evidence_id uuid,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_entity_alias_value
                UNIQUE (organization_id, entity_id, alias_type, normalized_value),
            CONSTRAINT fk_market_entity_alias_entity_same_org
                FOREIGN KEY (organization_id, entity_id)
                REFERENCES market_entities (organization_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_entity_alias_evidence_same_org
                FOREIGN KEY (organization_id, source_evidence_id)
                REFERENCES evidence (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_entity_aliases_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_entity_aliases_organization_id "
        "ON market_entity_aliases (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_entity_aliases_org_lookup "
        "ON market_entity_aliases (organization_id, alias_type, normalized_value)"
    )

    op.execute(
        """
        CREATE TABLE market_workspace_entities (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            entity_id uuid NOT NULL,
            discovery_reason text,
            relevance_note text,
            status text NOT NULL DEFAULT 'DISCOVERED',
            first_seen_at timestamptz NOT NULL DEFAULT now(),
            last_seen_at timestamptz NOT NULL DEFAULT now(),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_workspace_entity
                UNIQUE (organization_id, workspace_id, entity_id),
            CONSTRAINT ck_market_workspace_entities_status_allowed
                CHECK (status IN ('DISCOVERED','RESEARCHING','INCLUDED','EXCLUDED')),
            CONSTRAINT fk_market_workspace_entity_workspace_same_org
                FOREIGN KEY (organization_id, workspace_id)
                REFERENCES market_workspaces (organization_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_workspace_entity_entity_same_org
                FOREIGN KEY (organization_id, entity_id)
                REFERENCES market_entities (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_workspace_entities_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_workspace_entities_organization_id "
        "ON market_workspace_entities (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_workspace_entities_org_workspace "
        "ON market_workspace_entities (organization_id, workspace_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_workspace_entities_org_entity "
        "ON market_workspace_entities (organization_id, entity_id)"
    )

    op.execute(
        """
        CREATE TABLE market_entity_customer_links (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            entity_id uuid NOT NULL,
            customer_id uuid NOT NULL,
            link_status text NOT NULL DEFAULT 'MATCH_CANDIDATE',
            matching_basis jsonb NOT NULL DEFAULT '{}',
            confirmed_by_user_id uuid,
            confirmed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_market_entity_customer_link
                UNIQUE (organization_id, entity_id, customer_id),
            CONSTRAINT ck_market_entity_customer_links_link_status_allowed
                CHECK (link_status IN ('MATCH_CANDIDATE','USER_CONFIRMED','REJECTED','CONFLICTED')),
            CONSTRAINT ck_market_entity_customer_links_confirmed_link_has_actor_and_time
                CHECK (
                    link_status <> 'USER_CONFIRMED'
                    OR (confirmed_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL)
                ),
            CONSTRAINT fk_market_entity_customer_link_entity_same_org
                FOREIGN KEY (organization_id, entity_id)
                REFERENCES market_entities (organization_id, id) ON DELETE CASCADE,
            CONSTRAINT fk_market_entity_customer_link_customer_same_org
                FOREIGN KEY (organization_id, customer_id)
                REFERENCES customers (organization_id, id) ON DELETE RESTRICT,
            CONSTRAINT fk_market_entity_customer_links_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_market_entity_customer_links_confirmed_by_user_id_users
                FOREIGN KEY (confirmed_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_market_entity_customer_links_organization_id "
        "ON market_entity_customer_links (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_market_entity_customer_links_org_customer "
        "ON market_entity_customer_links (organization_id, customer_id)"
    )

    for table in TABLES:
        helpers.enable_org_rls(table, force=True)
    helpers.ensure_updated_at_trigger(
        "market_entities",
        "market_workspace_entities",
        "market_entity_customer_links",
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        helpers.disable_org_rls(table)
    op.execute("DROP TABLE IF EXISTS market_entity_customer_links")
    op.execute("DROP TABLE IF EXISTS market_workspace_entities")
    op.execute("DROP TABLE IF EXISTS market_entity_aliases")
    op.execute("DROP TABLE IF EXISTS market_entities")
    op.execute("ALTER TABLE evidence DROP CONSTRAINT IF EXISTS uq_evidence_org_id")
    op.execute("ALTER TABLE customers DROP CONSTRAINT IF EXISTS uq_customers_org_id")
