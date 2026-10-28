"""迁移公共助手。只被 ``migrations/versions/*`` 调用，不被应用代码导入。

**RLS 落地方式**：本项目采用"建表即授权"——每个创建组织级表的迁移，立刻对这批表启用
``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` 并创建 ``tenant_isolation`` 策略；
``0013_security_rls`` 再做一次全库幂等扫描作为兜底与验收。
这样在任一中间版本上租户隔离都已生效，而不是等到最后一个迁移才补。
理由与代价见 ``docs/decisions/ADR-0002-rls-per-migration.md``。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

POLICY = "tenant_isolation"

#: 标准谓词。``nullif(..., '')`` 把"未设置"与"空串"统一归一为 NULL，
#: 策略结果为 NULL ⇒ 行不可见、写入被拒 —— 即 fail-closed。
ORG_PREDICATE = "organization_id = nullif(current_setting('app.current_org', true), '')::uuid"
SELF_PREDICATE = "id = nullif(current_setting('app.current_org', true), '')::uuid"
MEMBER_PREDICATE = (
    "EXISTS (SELECT 1 FROM memberships m WHERE m.user_id = users.id "
    "AND m.organization_id = nullif(current_setting('app.current_org', true), '')::uuid)"
)


def role_exists(role: str) -> bool:
    """引导脚本未跑过（例如裸库）时，跳过 GRANT 而不是让迁移失败。"""
    bind = op.get_bind()
    return bind.execute(sa.text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": role}).scalar() == 1


def enable_org_rls(
    table: str,
    *,
    predicate: str = ORG_PREDICATE,
    app_role: str = "gbt_app",
    force: bool = True,
) -> None:
    """启用组织级隔离策略。

    ``force=True``（业务表默认）：连表 owner 也受策略约束，需显式 ``set_config`` 才可读写，
    用于防住"用 owner 连接绕过隔离"这类误用。

    ``force=False``（身份三表专用）：owner 仍可绕过，因为**身份数据的一次性开通**（建组织、
    建用户、建成员关系）本身就是特权引导操作，而且 ``users`` 的策略依赖 ``memberships``
    存在，插入时存在先有鸡还是先有蛋的问题。非 owner 的 ``gbt_app`` 依然严格受限。
    取舍理由见 ``docs/decisions/ADR-0002-rls-per-migration.md``。
    """
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} {'FORCE' if force else 'NO FORCE'} ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
    op.execute(
        f"CREATE POLICY {POLICY} ON {table} FOR ALL USING ({predicate}) WITH CHECK ({predicate})"
    )
    if role_exists(app_role):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {app_role}")


def disable_org_rls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS {POLICY} ON {table}")
    op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")


def ensure_updated_at_trigger(*tables: str) -> None:
    """给带 ``updated_at`` 的表挂统一触发器。

    模型层的 ``onupdate=func.now()`` 只覆盖 ORM 发出的 UPDATE；裸 SQL 更新（如数据修复、
    其它服务写入）也必须刷新 ``updated_at``，否则审计与竞态判断会读到过期时间。
    """
    op.execute(
        """
        CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
        BEGIN
            NEW.updated_at := now();
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
        """
    )
    for table in tables:
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated_at ON {table}")
        op.execute(
            f"CREATE TRIGGER trg_{table}_updated_at BEFORE UPDATE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )


def ensure_places_geog_trigger() -> None:
    """``places.geog`` 是派生列，必须由数据库维护，避免应用层漏写导致空间索引失真。"""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION sync_place_geog() RETURNS trigger AS $$
        BEGIN
            IF NEW.lat IS NOT NULL AND NEW.lon IS NOT NULL THEN
                NEW.geog := ST_SetSRID(ST_MakePoint(NEW.lon, NEW.lat), 4326)::geography;
            ELSE
                NEW.geog := NULL;
            END IF;
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql
        """
    )
    op.execute("DROP TRIGGER IF EXISTS trg_places_geog ON places")
    op.execute(
        "CREATE TRIGGER trg_places_geog BEFORE INSERT OR UPDATE OF lat, lon ON places "
        "FOR EACH ROW EXECUTE FUNCTION sync_place_geog()"
    )