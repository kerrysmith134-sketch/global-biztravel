"""0031_visa_catalog_overrides — 签证要求目录的组织级覆盖（ADR-0034 网页形式兜底）。

背景：签证要求没有免费官方 API（Timatic 收费）。离线目录（``app/data/visa_catalog.json``）
保持只读基线；本迁移给 ADMIN 提供"网页维护"的落点——对目录条目的修改落在覆盖表，
读取时合并覆盖在基线之上。目录仍是单一真相（基线 + 显式覆盖），不引入第二套数据源。

Revision ID: 0031_visa_catalog_overrides
Revises: 0030_traveler_nationality
Create Date: 2026-11-12
"""

from alembic import op

revision = "0031_visa_catalog_overrides"
down_revision = "0030_traveler_nationality"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE visa_catalog_overrides (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            country_code    char(2) NOT NULL,
            entry           jsonb NOT NULL,
            updated_by_user_id uuid,
            created_at      timestamptz NOT NULL DEFAULT now(),
            updated_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_visa_overrides_org_country
                UNIQUE (organization_id, country_code),
            CONSTRAINT fk_visa_overrides_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_visa_overrides_updated_by_users
                FOREIGN KEY (updated_by_user_id) REFERENCES users (id) ON DELETE SET NULL
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_visa_overrides_organization_id "
        "ON visa_catalog_overrides (organization_id)"
    )
    op.execute(
        "ALTER TABLE visa_catalog_overrides ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE visa_catalog_overrides FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        """
        CREATE POLICY tenant_isolation ON visa_catalog_overrides
            USING (organization_id = current_setting('app.current_org', true)::uuid)
            WITH CHECK (organization_id = current_setting('app.current_org', true)::uuid)
        """
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON visa_catalog_overrides TO gbt_app"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS visa_catalog_overrides")
