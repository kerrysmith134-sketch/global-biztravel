"""0006_visit_records — 现场拜访记录。

设计文档 v2.0 第 10.5 章（离线同步冲突矩阵）与第 17.2 章 MS-5。

为什么拜访记录要单独一张表，而不是往 ``visits`` 上加几列：

- **一条拜访可能被多次回填**（先到现场记「未会面」，改期后再记「已会面」）。把它们压成
  visits 上的当前值会丢掉历史，而现场记录恰恰是审计与复盘（第 9 章）的原料。
- **冲突副本必须保留**。LWW 覆盖的是"生效值"，不是"证据"；落败的提交以 ``applied=false``
  存下来，人工才能看到两个版本。
- ``client_record_id`` 唯一约束让弱网重试与离线队列重放天然幂等。

Revision ID: 0006_visit_records
Revises: 0005_proactive_runtime
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0006_visit_records"
down_revision = "0005_proactive_runtime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE visit_records (
            id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id    uuid NOT NULL,
            visit_id           uuid NOT NULL,
            client_record_id   text NOT NULL,
            outcome            text NOT NULL,
            notes              text NOT NULL DEFAULT '',
            photos             jsonb NOT NULL DEFAULT '[]',
            follow_up_tasks    jsonb NOT NULL DEFAULT '[]',
            client_updated_at  timestamptz NOT NULL,
            server_version     integer NOT NULL DEFAULT 0,
            applied            boolean NOT NULL DEFAULT true,
            conflict_reason    text,
            created_by_user_id uuid,
            created_at         timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_visit_records_org_client_record
                UNIQUE (organization_id, client_record_id),
            CONSTRAINT ck_visit_records_outcome_allowed
                CHECK (outcome IN ('MET','NOT_MET','RESCHEDULED','CANCELLED')),
            CONSTRAINT ck_visit_records_server_version_non_negative
                CHECK (server_version >= 0),
            CONSTRAINT fk_visit_records_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_visit_records_visit_id_visits
                FOREIGN KEY (visit_id) REFERENCES visits (id) ON DELETE CASCADE,
            CONSTRAINT fk_visit_records_created_by_user_id_users
                FOREIGN KEY (created_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute("CREATE INDEX ix_visit_records_organization_id ON visit_records (organization_id)")
    op.execute("CREATE INDEX ix_visit_records_visit_id ON visit_records (visit_id)")
    op.execute(
        "CREATE INDEX ix_visit_records_visit_id_client_updated_at "
        "ON visit_records (visit_id, client_updated_at)"
    )

    helpers.enable_org_rls("visit_records", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("visit_records")
    op.execute("DROP TABLE IF EXISTS visit_records")
