"""0027_assistant_conversations — AI 助手对话（PRD 页面 05）。

对话记录是**独立的工作副本**：删除对话只删这两张表，不触碰任务/计划/审计
（PRD 6.1：清聊天不能取消工作流，反之亦然）。

Revision ID: 0027_assistant_conversations
Revises: 0026_notification_priority
Create Date: 2026-10-29
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0027_assistant_conversations"
down_revision = "0026_notification_priority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE assistant_conversations (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            user_id         uuid,
            trip_id         uuid,
            title           text NOT NULL DEFAULT '',
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_assistant_conversations_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_assistant_conversations_user_id_users
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT fk_assistant_conversations_trip_id_trips
                FOREIGN KEY (trip_id) REFERENCES trips (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_assistant_conversations_org_user_updated "
        "ON assistant_conversations (organization_id, user_id, updated_at)"
    )
    op.execute(
        "CREATE INDEX ix_assistant_conversations_organization_id "
        "ON assistant_conversations (organization_id)"
    )
    op.execute(
        "CREATE INDEX ix_assistant_conversations_user_id "
        "ON assistant_conversations (user_id)"
    )
    op.execute(
        """
        CREATE TABLE assistant_messages (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id   uuid NOT NULL,
            conversation_id   uuid NOT NULL,
            role              text NOT NULL,
            content           text NOT NULL DEFAULT '',
            cards             jsonb NOT NULL DEFAULT '[]',
            llm_invocation_id uuid,
            created_at        timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_assistant_messages_role_allowed
                CHECK (role IN ('USER','ASSISTANT')),
            CONSTRAINT fk_assistant_messages_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_assistant_messages_conversation_id_assistant_conversations
                FOREIGN KEY (conversation_id) REFERENCES assistant_conversations (id) ON DELETE CASCADE,
            CONSTRAINT fk_assistant_messages_llm_invocation_id_llm_invocations
                FOREIGN KEY (llm_invocation_id) REFERENCES llm_invocations (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_assistant_messages_conversation_created "
        "ON assistant_messages (conversation_id, created_at)"
    )
    op.execute(
        "CREATE INDEX ix_assistant_messages_organization_id "
        "ON assistant_messages (organization_id)"
    )
    helpers.ensure_updated_at_trigger("assistant_conversations")
    helpers.ensure_updated_at_trigger("assistant_messages")
    helpers.enable_org_rls("assistant_conversations", force=True)
    helpers.enable_org_rls("assistant_messages", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("assistant_messages")
    helpers.disable_org_rls("assistant_conversations")
    op.execute("DROP TABLE IF EXISTS assistant_messages")
    op.execute("DROP TABLE IF EXISTS assistant_conversations")
