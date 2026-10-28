"""0011_external_signals — EXTERNAL 触发源的输入落点。

设计文档 v2.0 第 14 章与 §8.1。外部源（签证政策/天气/汇率/航班）不在本系统内，但它们的
变化必须能被主动式运行时消费。本表只保存"外部源报告的事实"，不保存推断结论；建议的生成、
抑制与人审仍由主动式服务负责。

``(organization_id, provider, dedupe_key)`` 唯一，配合 ingest 的 ``ON CONFLICT DO UPDATE``
让重复推送幂等；``valid_until`` 让过期信号自然退出扫描窗口。

Revision ID: 0011_external_signals
Revises: 0010_security_rls
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0011_external_signals"
down_revision = "0010_security_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE external_signals (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            provider        text NOT NULL,
            signal_type     text NOT NULL,
            subject_type    text NOT NULL,
            subject_ref     text NOT NULL,
            payload         jsonb NOT NULL DEFAULT '{}',
            confidence      double precision NOT NULL DEFAULT 0.7,
            observed_at     timestamptz NOT NULL DEFAULT now(),
            valid_from      timestamptz NOT NULL DEFAULT now(),
            valid_until     timestamptz,
            dedupe_key      text NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_external_signals_org_provider_dedupe
                UNIQUE (organization_id, provider, dedupe_key),
            CONSTRAINT ck_external_signals_signal_type_allowed
                CHECK (signal_type IN ('VISA_POLICY_CHANGE','WEATHER_DISRUPTION',
                                       'FX_VOLATILITY','FLIGHT_STATUS')),
            CONSTRAINT ck_external_signals_subject_type_allowed
                CHECK (subject_type IN ('COUNTRY','TRIP','CUSTOMER')),
            CONSTRAINT ck_external_signals_confidence_range
                CHECK (confidence >= 0 AND confidence <= 1),
            CONSTRAINT fk_external_signals_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_external_signals_organization_id ON external_signals (organization_id)")
    op.execute(
        "CREATE INDEX ix_external_signals_org_subject "
        "ON external_signals (organization_id, subject_type, subject_ref)"
    )
    op.execute(
        "CREATE INDEX ix_external_signals_org_signal_type "
        "ON external_signals (organization_id, signal_type)"
    )
    helpers.ensure_updated_at_trigger("external_signals")
    helpers.enable_org_rls("external_signals", force=True)


def downgrade() -> None:
    helpers.disable_org_rls("external_signals")
    op.execute("DROP TABLE IF EXISTS external_signals")
