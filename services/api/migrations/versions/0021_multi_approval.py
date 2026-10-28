"""0021_multi_approval — 可配置多人会签。

企业版需要"两个 ADMIN 都批准才执行"，而原 ``approvals`` 对同一对象只允许一条记录。本迁移：

- ``approval_policies.required_approvals``（默认 1，保持既有行为）；
- 去掉 ``approvals`` 的"每对象一条"唯一约束，改为"同一对象同一审批人一条"；
- 服务层统计不同审批人，达到阈值才执行。

Revision ID: 0021_multi_approval
Revises: 0020_travel_policies
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

revision = "0021_multi_approval"
down_revision = "0020_travel_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE approval_policies
            ADD COLUMN required_approvals integer NOT NULL DEFAULT 1,
            ADD CONSTRAINT ck_approval_policies_required_approvals_positive
                CHECK (required_approvals >= 1)
        """
    )
    op.execute("ALTER TABLE approvals DROP CONSTRAINT IF EXISTS uq_approvals_org_subject")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_approvals_org_subject_approver
        ON approvals (organization_id, subject_type, subject_id, decided_by_user_id)
        WHERE decided_by_user_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_approvals_org_subject_approver")
    op.execute(
        "ALTER TABLE approvals ADD CONSTRAINT uq_approvals_org_subject "
        "UNIQUE (organization_id, subject_type, subject_id)"
    )
    op.execute(
        "ALTER TABLE approval_policies DROP CONSTRAINT IF EXISTS ck_approval_policies_required_approvals_positive"
    )
    op.execute("ALTER TABLE approval_policies DROP COLUMN IF EXISTS required_approvals")
