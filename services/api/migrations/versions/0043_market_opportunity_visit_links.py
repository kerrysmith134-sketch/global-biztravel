"""0043_market_opportunity_visit_links — connect qualified opportunities to planned Trip visits."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from migrations import helpers

revision = "0043_market_opp_visit_link"
down_revision = "0042_market_claim_lineage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_visits_org_trip_id",
        "visits",
        ["organization_id", "trip_id", "id"],
    )
    op.create_table(
        "market_opportunity_visit_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("opportunity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trip_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("visit_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("link_status", sa.Text(), nullable=False, server_default="LINKED"),
        sa.Column("link_note", sa.Text(), nullable=True),
        sa.Column("linked_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("unlinked_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("unlinked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unlink_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="CASCADE",
            name="fk_market_opportunity_visit_links_organization_id_organizations",
        ),
        sa.ForeignKeyConstraint(
            ["linked_by_user_id"],
            ["users.id"],
            ondelete="SET NULL",
            name="fk_market_opportunity_visit_links_linked_by_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["unlinked_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
            name="fk_market_opportunity_visit_links_unlinked_by_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id", "opportunity_id"],
            [
                "market_opportunities.organization_id",
                "market_opportunities.workspace_id",
                "market_opportunities.id",
            ],
            ondelete="CASCADE",
            name="fk_market_opp_visit_link_opportunity_same_workspace",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "workspace_id", "trip_id"],
            [
                "market_workspace_trip_links.organization_id",
                "market_workspace_trip_links.workspace_id",
                "market_workspace_trip_links.trip_id",
            ],
            ondelete="CASCADE",
            name="fk_market_opp_visit_link_workspace_trip",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "trip_id", "visit_id"],
            ["visits.organization_id", "visits.trip_id", "visits.id"],
            ondelete="RESTRICT",
            name="fk_market_opp_visit_link_visit_same_trip",
        ),
        sa.UniqueConstraint(
            "organization_id", "id",
            name="uq_market_opp_visit_links_org_id",
        ),
        sa.UniqueConstraint(
            "organization_id", "workspace_id", "opportunity_id", "trip_id", "visit_id",
            name="uq_market_opp_visit_link_target",
        ),
        sa.CheckConstraint(
            "link_status IN ('LINKED','UNLINKED')",
            name="ck_market_opportunity_visit_links_link_status_allowed",
        ),
        sa.CheckConstraint(
            "link_status <> 'UNLINKED' OR "
            "(unlinked_by_user_id IS NOT NULL AND unlinked_at IS NOT NULL)",
            name="ck_market_opportunity_visit_links_unlinked_has_actor_and_time",
        ),
    )
    op.create_index(
        "ix_market_opportunity_visit_links_organization_id",
        "market_opportunity_visit_links",
        ["organization_id"],
    )
    op.create_index(
        "ix_market_opp_visit_links_org_workspace_status",
        "market_opportunity_visit_links",
        ["organization_id", "workspace_id", "opportunity_id", "link_status"],
    )
    op.create_index(
        "ix_market_opp_visit_links_org_trip_visit",
        "market_opportunity_visit_links",
        ["organization_id", "trip_id", "visit_id"],
    )
    # Every organization-scoped table must enable and force tenant RLS at creation.
    helpers.enable_org_rls("market_opportunity_visit_links", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("market_opportunity_visit_links")
    op.drop_index(
        "ix_market_opp_visit_links_org_trip_visit",
        table_name="market_opportunity_visit_links",
    )
    op.drop_index(
        "ix_market_opp_visit_links_org_workspace_status",
        table_name="market_opportunity_visit_links",
    )
    op.drop_index(
        "ix_market_opp_visit_links_organization_id",
        table_name="market_opportunity_visit_links",
    )
    op.drop_table("market_opportunity_visit_links")
    op.drop_constraint("uq_visits_org_trip_id", "visits", type_="unique")
