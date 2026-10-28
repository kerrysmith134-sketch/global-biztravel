"""0019_trip_expenses — 差旅费用归集。

PRD 方向六的最小闭环：按出行记录费用（类别/金额/币种/日期/凭证引用），与预算和计划费用对比。
多币种不换算，分币种汇总；只有同币种才计算差额。

Revision ID: 0019_trip_expenses
Revises: 0018_visa_cases
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0019_trip_expenses"
down_revision = "0018_visa_cases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE trip_expenses (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id    uuid NOT NULL,
            trip_id            uuid NOT NULL,
            category           text NOT NULL,
            amount             numeric(14,2) NOT NULL,
            currency           char(3) NOT NULL,
            occurred_on        date NOT NULL,
            note               text,
            receipt_ref        text,
            created_by_user_id uuid,
            created_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_trip_expenses_category_allowed
                CHECK (category IN ('TRANSPORT','LODGING','MEALS','OTHER')),
            CONSTRAINT ck_trip_expenses_amount_positive CHECK (amount > 0),
            CONSTRAINT fk_trip_expenses_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_trip_expenses_trip_id_trips
                FOREIGN KEY (trip_id) REFERENCES trips (id) ON DELETE CASCADE,
            CONSTRAINT fk_trip_expenses_created_by_user_id_users
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_trip_expenses_organization_id ON trip_expenses (organization_id)")
    op.execute("CREATE INDEX ix_trip_expenses_trip_id ON trip_expenses (trip_id)")
    op.execute(
        "CREATE INDEX ix_trip_expenses_org_trip_date "
        "ON trip_expenses (organization_id, trip_id, occurred_on)"
    )
    helpers.enable_org_rls("trip_expenses", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("trip_expenses")
    op.execute("DROP TABLE IF EXISTS trip_expenses")
