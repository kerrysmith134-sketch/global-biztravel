"""0028_notification_preferences — 用户通知偏好（PRD S8-08：按用户偏好动态调整）。

Revision ID: 0028_notification_preferences
Revises: 0027_assistant_conversations
Create Date: 2026-10-29
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0028_notification_preferences"
down_revision = "0027_assistant_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE notification_preferences (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id   uuid NOT NULL,
            user_id           uuid NOT NULL,
            muted_priorities  jsonb NOT NULL DEFAULT '[]',
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_notification_preferences_org_user
                UNIQUE (organization_id, user_id),
            CONSTRAINT fk_notification_preferences_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_notification_preferences_user_id_users
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_notification_preferences_organization_id "
        "ON notification_preferences (organization_id)"
    )
    helpers.ensure_updated_at_trigger("notification_preferences")
    helpers.enable_org_rls("notification_preferences", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("notification_preferences")
    op.execute("DROP TABLE IF EXISTS notification_preferences")
