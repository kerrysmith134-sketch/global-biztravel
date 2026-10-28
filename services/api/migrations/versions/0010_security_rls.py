"""0010_security_rls — 全库 RLS 兜底扫描。

前九批迁移已经按 ADR-0002 的「建表即授权」逐表启用 RLS；本迁移再做一次全库幂等扫描，
作为新增表的最后一道保险：

- 找出所有带 ``organization_id`` 列的业务表；
- 对它们 ``ENABLE`` + ``FORCE ROW LEVEL SECURITY`` 并重建 ``tenant_isolation`` 策略；
- **跳过 ``memberships``**：身份开通是特权引导操作，且 ``users`` 的策略依赖成员关系，
  强制 owner 受策略约束会造出"先有鸡还是先有蛋"的死锁（见 ADR-0002 的取舍说明）。

本迁移可以安全重复执行（幂等），因此即使以后某张表漏写 RLS，也可以在发布脚本里重跑它。

Revision ID: 0010_security_rls
Revises: 0009_metric_snapshots
Create Date: 2026-10-28
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

from migrations import helpers

revision = "0010_security_rls"
down_revision = "0009_metric_snapshots"
branch_labels = None
depends_on = None

#: 身份开通表：允许 owner 在无租户 GUC 时完成组织/成员引导（详见 ADR-0002）。
EXEMPT = frozenset({"memberships", "organization_invitations"})


def _org_tables() -> list[str]:
    bind = op.get_bind()
    rows = bind.execute(
        text(
            """
            SELECT table_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND column_name = 'organization_id'
            ORDER BY table_name
            """
        )
    ).scalars()
    return [name for name in rows if name not in EXEMPT]


def upgrade() -> None:
    for table in _org_tables():
        helpers.enable_org_rls(table, force=True)


def downgrade() -> None:
    # 不回滚：本批是幂等的确认动作，之前的迁移各自负责自己的 RLS 生命周期。
    # 强行 disable 会把前九批的隔离一起拆掉，风险远大于"回滚不彻底"。
    pass
