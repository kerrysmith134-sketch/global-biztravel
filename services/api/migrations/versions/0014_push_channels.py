"""0014_push_channels — 设备 token 与通知收件人。

设计文档第 8.6 章：推送通道需要可管理的设备 token（APNs）与邮箱（SMTP）。
通知在入队时把收件人快照到 ``notifications.recipient``，投递只读取快照，避免重试期间收件人
变化导致发错对象。

Revision ID: 0014_push_channels
Revises: 0013_approval_policies
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0014_push_channels"
down_revision = "0013_approval_policies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE notifications ADD COLUMN recipient text")

    op.execute(
        """
        CREATE TABLE device_tokens (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            user_id         uuid NOT NULL,
            platform        text NOT NULL DEFAULT 'IOS',
            token           text NOT NULL,
            enabled         boolean NOT NULL DEFAULT true,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            last_seen_at    timestamptz,
            CONSTRAINT uq_device_tokens_org_token UNIQUE (organization_id, token),
            CONSTRAINT ck_device_tokens_platform_allowed CHECK (platform IN ('IOS','ANDROID')),
            CONSTRAINT fk_device_tokens_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_device_tokens_user_id_users
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_device_tokens_organization_id ON device_tokens (organization_id)")
    op.execute("CREATE INDEX ix_device_tokens_user_id ON device_tokens (user_id)")
    op.execute(
        "CREATE INDEX ix_device_tokens_org_user ON device_tokens (organization_id, user_id, enabled)"
    )
    helpers.ensure_updated_at_trigger("device_tokens")
    helpers.enable_org_rls("device_tokens", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("device_tokens")
    op.execute("DROP TABLE IF EXISTS device_tokens")
    op.execute("ALTER TABLE notifications DROP COLUMN IF EXISTS recipient")
