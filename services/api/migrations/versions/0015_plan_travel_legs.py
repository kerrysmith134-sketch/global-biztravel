"""0015_plan_travel_legs — 求解状态、交通段与费用。

PRD MVP-1 基线 §5.2/§5.3/DDL §4.3：

- 计划结果必须有显式状态（OPTIMAL/FEASIBLE/PARTIAL/INFEASIBLE/UNKNOWN/ERROR），
  `FEASIBLE` 不得展示为“全局最优”；
- 交通段（`plan_travel_legs`）的起点/终点使用真实外键，不能是多态 UUID；
- 费用分项与未知费用要可区分：未知记为 NULL，绝不记为 0。

原有 `plan_stops.travel_minutes` 保留，legs 提供逐段来源、距离、费用与 stale 标记。

Revision ID: 0015_plan_travel_legs
Revises: 0014_push_channels
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0015_plan_travel_legs"
down_revision = "0014_push_channels"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE plan_versions
            ADD COLUMN solution_status text NOT NULL DEFAULT 'FEASIBLE',
            ADD COLUMN cost_total double precision,
            ADD COLUMN cost_currency char(3),
            ADD COLUMN cost_unknown_count integer NOT NULL DEFAULT 0,
            ADD CONSTRAINT ck_plan_versions_solution_status_allowed
                CHECK (solution_status IN ('OPTIMAL','FEASIBLE','PARTIAL','INFEASIBLE','UNKNOWN','ERROR')),
            ADD CONSTRAINT ck_plan_versions_cost_unknown_count_non_negative
                CHECK (cost_unknown_count >= 0)
        """
    )
    op.execute(
        """
        ALTER TABLE plan_stops
            ADD COLUMN distance_km double precision,
            ADD COLUMN cost_amount double precision,
            ADD COLUMN cost_currency char(3),
            ADD COLUMN travel_source text NOT NULL DEFAULT 'haversine-estimate',
            ADD COLUMN travel_stale boolean NOT NULL DEFAULT false
        """
    )

    op.execute(
        """
        CREATE TABLE plan_travel_legs (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            plan_version_id uuid NOT NULL,
            from_stop_id    uuid,
            to_stop_id      uuid NOT NULL,
            day_index       integer NOT NULL,
            mode            text NOT NULL,
            travel_minutes  integer NOT NULL DEFAULT 0,
            distance_km     double precision NOT NULL DEFAULT 0,
            cost_amount     double precision,
            cost_currency   char(3),
            source          text NOT NULL DEFAULT 'haversine-estimate',
            stale           boolean NOT NULL DEFAULT false,
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_plan_travel_legs_day_index_non_negative CHECK (day_index >= 0),
            CONSTRAINT ck_plan_travel_legs_travel_minutes_non_negative CHECK (travel_minutes >= 0),
            CONSTRAINT ck_plan_travel_legs_distance_non_negative CHECK (distance_km >= 0),
            CONSTRAINT ck_plan_travel_legs_mode_allowed
                CHECK (mode IN ('GROUND','RAIL','AIR','SEA')),
            CONSTRAINT fk_plan_travel_legs_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_plan_travel_legs_plan_version_id_plan_versions
                FOREIGN KEY (plan_version_id) REFERENCES plan_versions (id) ON DELETE CASCADE,
            CONSTRAINT fk_plan_travel_legs_from_stop_id_plan_stops
                FOREIGN KEY (from_stop_id) REFERENCES plan_stops (id) ON DELETE CASCADE,
            CONSTRAINT fk_plan_travel_legs_to_stop_id_plan_stops
                FOREIGN KEY (to_stop_id) REFERENCES plan_stops (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_plan_travel_legs_organization_id ON plan_travel_legs (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_plan_travel_legs_plan_version_id ON plan_travel_legs (plan_version_id)"
    )
    op.execute(
        "CREATE INDEX ix_plan_travel_legs_to_stop_id ON plan_travel_legs (to_stop_id)"
    )
    helpers.enable_org_rls("plan_travel_legs", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("plan_travel_legs")
    op.execute("DROP TABLE IF EXISTS plan_travel_legs")
    op.execute("ALTER TABLE plan_stops DROP COLUMN IF EXISTS travel_stale")
    op.execute("ALTER TABLE plan_stops DROP COLUMN IF EXISTS travel_source")
    op.execute("ALTER TABLE plan_stops DROP COLUMN IF EXISTS cost_currency")
    op.execute("ALTER TABLE plan_stops DROP COLUMN IF EXISTS cost_amount")
    op.execute("ALTER TABLE plan_stops DROP COLUMN IF EXISTS distance_km")
    op.execute(
        "ALTER TABLE plan_versions DROP CONSTRAINT IF EXISTS ck_plan_versions_cost_unknown_count_non_negative"
    )
    op.execute(
        "ALTER TABLE plan_versions DROP CONSTRAINT IF EXISTS ck_plan_versions_solution_status_allowed"
    )
    op.execute("ALTER TABLE plan_versions DROP COLUMN IF EXISTS cost_unknown_count")
    op.execute("ALTER TABLE plan_versions DROP COLUMN IF EXISTS cost_currency")
    op.execute("ALTER TABLE plan_versions DROP COLUMN IF EXISTS cost_total")
    op.execute("ALTER TABLE plan_versions DROP COLUMN IF EXISTS solution_status")
