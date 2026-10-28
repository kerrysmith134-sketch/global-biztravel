"""0001_core_identity — 组织 / 用户 / 成员。

设计文档 v2.0 第 12.2 章迁移批次第 1 项。本批只建身份三表，不含业务数据。

约定：
- 所有约束/索引**显式命名**，且与 ``app.models.base.NAMING_CONVENTION`` 完全一致，
  这样 ``alembic check`` 在 head 上保持干净（不产生命名漂移噪音）。
- 本批在**自己建表的同时**启用租户隔离（"建表即授权"）：身份三表 ``ENABLE`` 但不
  ``FORCE``，保留一次性的身份开通能力；非 owner 的 ``gbt_app`` 依然严格受限。
  理由与取舍见 ``docs/decisions/ADR-0002-rls-per-migration.md``。
- ``set_updated_at()`` 函数与 ``updated_at`` 触发器由本批创建，供后续迁移复用。

Revision ID: 0001_core_identity
Revises:
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op

from migrations import helpers

revision = "0001_core_identity"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE organizations (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            name                 text NOT NULL,
            slug                 text NOT NULL,
            default_timezone     text NOT NULL DEFAULT 'Asia/Shanghai',
            default_currency     char(3) NOT NULL DEFAULT 'CNY',
            daily_suggestion_cap smallint NOT NULL DEFAULT 3,
            created_at           timestamptz NOT NULL DEFAULT now(),
            updated_at           timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_organizations_slug UNIQUE (slug)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE users (
            id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            email         text NOT NULL,
            display_name  text NOT NULL,
            home_timezone text NOT NULL DEFAULT 'Asia/Shanghai',
            created_at    timestamptz NOT NULL DEFAULT now(),
            updated_at    timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_users_email UNIQUE (email)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE memberships (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            organization_id uuid NOT NULL,
            user_id         uuid NOT NULL,
            role            text NOT NULL DEFAULT 'TRAVELER',
            created_at      timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_memberships_organization_id_user_id UNIQUE (organization_id, user_id),
            CONSTRAINT ck_memberships_role_allowed
                CHECK (role IN ('OWNER', 'ADMIN', 'TRAVELER', 'VIEWER')),
            CONSTRAINT fk_memberships_organization_id_organizations
                FOREIGN KEY (organization_id) REFERENCES organizations (id) ON DELETE CASCADE,
            CONSTRAINT fk_memberships_user_id_users
                FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
        """
    )

    op.execute("CREATE INDEX ix_memberships_organization_id ON memberships (organization_id)")
    op.execute("CREATE INDEX ix_memberships_user_id ON memberships (user_id)")

    # --------------------------------------------------------------- 触发器
    helpers.ensure_updated_at_trigger("organizations", "users")

    # ------------------------------------------------------------------- RLS
    # 身份三表只 ENABLE、不 FORCE：owner 仍可完成一次性的身份开通（建组织/建用户/建成员
    # 关系），而且 ``users`` 的策略依赖 ``memberships`` 存在，FORCE 会让"第一次开通"
    # 在逻辑上无法完成。非 owner 的 gbt_app 依然严格受限。见 ADR-0002。
    helpers.enable_org_rls("memberships", predicate=helpers.ORG_PREDICATE, force=False)
    helpers.enable_org_rls("organizations", predicate=helpers.SELF_PREDICATE, force=False)
    helpers.enable_org_rls("users", predicate=helpers.MEMBER_PREDICATE, force=False)


def downgrade() -> None:
    for table in ("memberships", "organizations", "users"):
        helpers.disable_org_rls(table)
    op.execute("DROP TABLE IF EXISTS memberships")
    op.execute("DROP TABLE IF EXISTS users")
    op.execute("DROP TABLE IF EXISTS organizations")
    # 三表已删，其 updated_at 触发器随之消失，函数此时才无依赖者。
    op.execute("DROP FUNCTION IF EXISTS set_updated_at()")
