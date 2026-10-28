"""0024_fx_rates — 组织级汇率快照。

汇率由管理员显式录入（1 base = rate quote），预算折算只使用最新一条；没有记录就不折算。

Revision ID: 0024_fx_rates
Revises: 0023_crm_sync
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0024_fx_rates"
down_revision = "0023_crm_sync"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE fx_rates (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id    uuid NOT NULL,
            base_currency      char(3) NOT NULL,
            quote_currency     char(3) NOT NULL,
            rate               numeric(18,8) NOT NULL,
            as_of              timestamptz NOT NULL,
            source             text,
            created_by_user_id uuid,
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_fx_rates_org_pair_as_of
                UNIQUE (organization_id, base_currency, quote_currency, as_of),
            CONSTRAINT ck_fx_rates_pair_distinct CHECK (base_currency <> quote_currency),
            CONSTRAINT ck_fx_rates_rate_positive CHECK (rate > 0),
            CONSTRAINT fk_fx_rates_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_fx_rates_created_by_user_id_users
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_fx_rates_organization_id ON fx_rates (organization_id)")
    op.execute(
        "CREATE INDEX ix_fx_rates_org_pair_as_of "
        "ON fx_rates (organization_id, base_currency, quote_currency, as_of)"
    )
    helpers.ensure_updated_at_trigger("fx_rates")
    helpers.enable_org_rls("fx_rates", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("fx_rates")
    op.execute("DROP TABLE IF EXISTS fx_rates")
