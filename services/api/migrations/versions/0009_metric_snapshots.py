"""0009_metric_snapshots — 指标时间序列。

设计文档 v2.0 第 16 章。一个指标一行、一个窗口一行，唯一键
``(organization_id, metric_key, window_start, window_end)`` 让 Worker 的周期快照可以安全重跑
（同窗口覆盖而不是堆重复行）。

Revision ID: 0009_metric_snapshots
Revises: 0008_llm_invocations
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0009_metric_snapshots"
down_revision = "0008_llm_invocations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE metric_snapshots (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            metric_key      text NOT NULL,
            value           double precision NOT NULL DEFAULT 0,
            sample_size     integer NOT NULL DEFAULT 0,
            window_start    timestamptz NOT NULL,
            window_end      timestamptz NOT NULL,
            dimensions      jsonb NOT NULL DEFAULT '{}',
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_metric_snapshots_org_key_window
                UNIQUE (organization_id, metric_key, window_start, window_end),
            CONSTRAINT ck_metric_snapshots_sample_size_non_negative CHECK (sample_size >= 0),
            CONSTRAINT fk_metric_snapshots_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_metric_snapshots_organization_id ON metric_snapshots (organization_id)")
    op.execute(
        "CREATE INDEX ix_metric_snapshots_org_key_created_at "
        "ON metric_snapshots (organization_id, metric_key, created_at)"
    )

    helpers.enable_org_rls("metric_snapshots", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("metric_snapshots")
    op.execute("DROP TABLE IF EXISTS metric_snapshots")
