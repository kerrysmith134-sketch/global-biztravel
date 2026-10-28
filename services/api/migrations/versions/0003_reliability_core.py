"""0003_reliability_core — 幂等记录表。

设计文档 v2.0 第 8.4 章（幂等）与第 12.2 章迁移批次。

**对批次顺序的有意调整**：设计文档把可靠性表放在 `0006/0008`，但首个写接口
（`POST /v1/trips`）在契约上就要接受 `Idempotency-Key`。让"先有幂等原语、后有写接口"，
比"先写接口、后补幂等"更安全——否则移动端在网络重试时会真的创建重复出行。
后台任务/Outbox 仍留在各自的迁移里。理由见 ADR-0002。

Revision ID: 0003_reliability_core
Revises: 0002_trip_domain
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0003_reliability_core"
down_revision = "0002_trip_domain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE idempotency_records (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            endpoint        text NOT NULL,
            key             text NOT NULL,
            request_hash    text NOT NULL,
            response_status integer NOT NULL,
            response_body   jsonb NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            expires_at      timestamptz,
            CONSTRAINT uq_idempotency_records_organization_id_endpoint_key
                UNIQUE (organization_id, endpoint, key),
            CONSTRAINT ck_idempotency_records_status_range
                CHECK (response_status >= 100 AND response_status <= 599),
            CONSTRAINT fk_idempotency_records_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_idempotency_records_organization_id ON idempotency_records (organization_id)"
    )
    op.execute("CREATE INDEX ix_idempotency_records_expires_at ON idempotency_records (expires_at)")
    helpers.enable_org_rls("idempotency_records", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("idempotency_records")
    op.execute("DROP TABLE IF EXISTS idempotency_records")