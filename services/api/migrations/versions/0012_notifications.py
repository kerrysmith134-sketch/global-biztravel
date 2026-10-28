"""0012_notifications — 主动建议的推送落点。

设计文档 v2.0 第 8.6 章。通知由 Outbox 消费者创建（"建议已提出"是事件），再由 Worker
按 provider 投递。``(organization_id, suggestion_id, provider)`` 唯一让重投幂等；失败重试
有次数上限与错误留痕。

Revision ID: 0012_notifications
Revises: 0011_external_signals
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0012_notifications"
down_revision = "0011_external_signals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE notifications (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            user_id         uuid,
            suggestion_id   uuid,
            kind            text NOT NULL DEFAULT 'PROACTIVE_SUGGESTION',
            title           text NOT NULL,
            body            text NOT NULL DEFAULT '',
            status          text NOT NULL DEFAULT 'PENDING',
            provider        text NOT NULL DEFAULT 'inapp',
            attempts        integer NOT NULL DEFAULT 0,
            sent_at         timestamptz,
            read_at         timestamptz,
            last_error      text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_notifications_org_suggestion_provider
                UNIQUE (organization_id, suggestion_id, provider),
            CONSTRAINT ck_notifications_status_allowed
                CHECK (status IN ('PENDING','SENT','FAILED')),
            CONSTRAINT ck_notifications_attempts_non_negative CHECK (attempts >= 0),
            CONSTRAINT fk_notifications_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_notifications_user_id_users
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT fk_notifications_suggestion_id_proactive_suggestions
                FOREIGN KEY (suggestion_id) REFERENCES proactive_suggestions (id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_notifications_organization_id ON notifications (organization_id)")
    op.execute("CREATE INDEX ix_notifications_user_id ON notifications (user_id)")
    op.execute(
        "CREATE INDEX ix_notifications_org_user_created_at "
        "ON notifications (organization_id, user_id, created_at)"
    )
    op.execute("CREATE INDEX ix_notifications_org_status ON notifications (organization_id, status)")
    helpers.ensure_updated_at_trigger("notifications")
    helpers.enable_org_rls("notifications", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("notifications")
    op.execute("DROP TABLE IF EXISTS notifications")
