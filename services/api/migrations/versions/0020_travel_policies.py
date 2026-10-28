"""0020_travel_policies — 差旅政策与计划违规记录。

政策规则由确定性比较执行（预算/提前天数/目的国/计划费用），不经过 LLM。计划生成时把违规
快照写入 ``plan_versions.policy_violations``，激活时若政策 ``strict`` 则拒绝。

Revision ID: 0020_travel_policies
Revises: 0019_trip_expenses
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0020_travel_policies"
down_revision = "0019_trip_expenses"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE travel_policies (
            id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id           uuid NOT NULL,
            name                      text NOT NULL,
            currency                  text NOT NULL DEFAULT 'CNY',
            max_trip_budget           numeric(14,2),
            max_plan_cost             numeric(14,2),
            min_advance_booking_days  integer,
            allowed_countries         text[] NOT NULL DEFAULT '{}',
            strict                    boolean NOT NULL DEFAULT false,
            enabled                   boolean NOT NULL DEFAULT true,
            created_at                timestamptz NOT NULL DEFAULT now(),
            updated_at                timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_travel_policies_org_name UNIQUE (organization_id, name),
            CONSTRAINT ck_travel_policies_trip_budget_non_negative
                CHECK (max_trip_budget IS NULL OR max_trip_budget >= 0),
            CONSTRAINT ck_travel_policies_plan_cost_non_negative
                CHECK (max_plan_cost IS NULL OR max_plan_cost >= 0),
            CONSTRAINT ck_travel_policies_advance_days_non_negative
                CHECK (min_advance_booking_days IS NULL OR min_advance_booking_days >= 0),
            CONSTRAINT fk_travel_policies_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_travel_policies_organization_id ON travel_policies (organization_id)")
    op.execute(
        "CREATE INDEX ix_travel_policies_org_enabled ON travel_policies (organization_id, enabled)"
    )
    op.execute(
        "ALTER TABLE plan_versions ADD COLUMN policy_violations jsonb NOT NULL DEFAULT '[]'"
    )
    helpers.ensure_updated_at_trigger("travel_policies")
    helpers.enable_org_rls("travel_policies", force=True)


def downgrade() -> None:
    op.execute("ALTER TABLE plan_versions DROP COLUMN IF EXISTS policy_violations")
    helpers.disable_org_rls("travel_policies")
    op.execute("DROP TABLE IF EXISTS travel_policies")
