"""0026_notification_priority — 通知等级与问题键（PRD S8-08）。

Revision ID: 0026_notification_priority
Revises: 0025_visit_objective
Create Date: 2026-10-29
"""

from __future__ import annotations

from alembic import op
revision = "0026_notification_priority"
down_revision = "0025_visit_objective"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE notifications ADD COLUMN priority text NOT NULL DEFAULT 'P2'")
    op.execute("ALTER TABLE notifications ADD COLUMN issue_key text")
    op.execute(
        """
        ALTER TABLE notifications
            ADD CONSTRAINT ck_notifications_priority_allowed
            CHECK (priority IN ('P0','P1','P2','P3'))
        """
    )
    op.execute(
        "CREATE INDEX ix_notifications_org_priority_created_at "
        "ON notifications (organization_id, priority, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_notifications_org_priority_created_at")
    op.execute("ALTER TABLE notifications DROP CONSTRAINT IF EXISTS ck_notifications_priority_allowed")
    op.execute("ALTER TABLE notifications DROP COLUMN IF EXISTS issue_key")
    op.execute("ALTER TABLE notifications DROP COLUMN IF EXISTS priority")
