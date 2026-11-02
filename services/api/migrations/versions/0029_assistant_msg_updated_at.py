"""0029_assistant_msg_updated_at — 补 assistant_messages.updated_at 列。

0027 给该表挂了 set_updated_at 触发器，但漏建 updated_at 列：纯 INSERT 不触发，
一旦 UPDATE（例如给消息回填 TRIP_PROPOSAL 卡片）就会报
``record "new" has no field "updated_at"``。本迁移补齐列，使触发器、模型与 DDL 一致。

Revision ID: 0029_assistant_msg_updated_at
Revises: 0028_notification_preferences
Create Date: 2026-11-02
"""

from __future__ import annotations

from alembic import op

revision = "0029_assistant_msg_updated_at"
down_revision = "0028_notification_preferences"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE assistant_messages "
        "ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE assistant_messages DROP COLUMN IF EXISTS updated_at")
