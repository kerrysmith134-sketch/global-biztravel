"""0022_expense_reports — 报销单与快照明细。

费用记录是原始数据；报销单在创建时把明细快照到 ``expense_report_items``，之后源费用被修改
不会改变已提交内容。状态 DRAFT → SUBMITTED → APPROVED/REJECTED；审批写 ``approvals``，
提交/决定写 ``workflow_instances``。

Revision ID: 0022_expense_reports
Revises: 0021_multi_approval
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0022_expense_reports"
down_revision = "0021_multi_approval"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE expense_reports (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id    uuid NOT NULL,
            trip_id            uuid NOT NULL,
            status             text NOT NULL DEFAULT 'DRAFT',
            notes              text,
            submitted_at       timestamptz,
            decided_at         timestamptz,
            decided_by_user_id uuid,
            created_by_user_id uuid,
            created_at         timestamptz NOT NULL DEFAULT now(),
            updated_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_expense_reports_status_allowed
                CHECK (status IN ('DRAFT','SUBMITTED','APPROVED','REJECTED')),
            CONSTRAINT fk_expense_reports_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_expense_reports_trip_id_trips
                FOREIGN KEY (trip_id) REFERENCES trips (id) ON DELETE CASCADE,
            CONSTRAINT fk_expense_reports_decided_by_user_id_users
                FOREIGN KEY (decided_by_user_id) REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT fk_expense_reports_created_by_user_id_users
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_expense_reports_organization_id ON expense_reports (organization_id)"
    )
    op.execute("CREATE INDEX ix_expense_reports_trip_id ON expense_reports (trip_id)")
    op.execute(
        "CREATE INDEX ix_expense_reports_org_trip ON expense_reports (organization_id, trip_id)"
    )
    op.execute(
        "CREATE INDEX ix_expense_reports_org_status ON expense_reports (organization_id, status)"
    )

    op.execute(
        """
        CREATE TABLE expense_report_items (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            report_id       uuid NOT NULL,
            expense_id      uuid,
            category        text NOT NULL,
            amount          numeric(14,2) NOT NULL,
            currency        char(3) NOT NULL,
            occurred_on     date NOT NULL,
            note            text,
            receipt_ref     text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_expense_report_items_amount_positive CHECK (amount > 0),
            CONSTRAINT ck_expense_report_items_category_allowed
                CHECK (category IN ('TRANSPORT','LODGING','MEALS','OTHER')),
            CONSTRAINT fk_expense_report_items_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_expense_report_items_report_id_expense_reports
                FOREIGN KEY (report_id) REFERENCES expense_reports (id) ON DELETE CASCADE,
            CONSTRAINT fk_expense_report_items_expense_id_trip_expenses
                FOREIGN KEY (expense_id) REFERENCES trip_expenses (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_expense_report_items_organization_id ON expense_report_items (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_expense_report_items_report_id ON expense_report_items (report_id)"
    )

    helpers.ensure_updated_at_trigger("expense_reports")
    for table in ("expense_reports", "expense_report_items"):
        helpers.enable_org_rls(table, force=True)


def downgrade() -> None:
    for table in ("expense_report_items", "expense_reports"):
        helpers.disable_org_rls(table)
    op.execute("DROP TABLE IF EXISTS expense_report_items")
    op.execute("DROP TABLE IF EXISTS expense_reports")
