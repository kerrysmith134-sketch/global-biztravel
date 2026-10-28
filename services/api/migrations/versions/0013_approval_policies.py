"""0013_approval_policies — 审批策略（N1 单级抽象）。

设计文档 N1 把多级审批列为后续演进；本次先落地企业最常用的单级策略：按建议风险等级要求
最低批准角色。没有策略时行为不变（默认放行有 TRAVELER 权限的批准）。

Revision ID: 0013_approval_policies
Revises: 0012_notifications
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0013_approval_policies"
down_revision = "0012_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE approval_policies (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            subject_type    text NOT NULL DEFAULT 'proactive_suggestion',
            min_severity    text NOT NULL DEFAULT 'LOW',
            required_role   text NOT NULL DEFAULT 'TRAVELER',
            enabled         boolean NOT NULL DEFAULT true,
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_approval_policies_org_subject_severity
                UNIQUE (organization_id, subject_type, min_severity),
            CONSTRAINT ck_approval_policies_role_allowed
                CHECK (required_role IN ('VIEWER','TRAVELER','ADMIN','OWNER')),
            CONSTRAINT ck_approval_policies_severity_allowed
                CHECK (min_severity IN ('LOW','MEDIUM','HIGH')),
            CONSTRAINT fk_approval_policies_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_approval_policies_organization_id ON approval_policies (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_approval_policies_org_enabled ON approval_policies (organization_id, enabled)"
    )
    helpers.enable_org_rls("approval_policies", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("approval_policies")
    op.execute("DROP TABLE IF EXISTS approval_policies")
