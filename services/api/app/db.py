"""数据库引擎、会话与租户上下文绑定。

设计文档 v2.0 落地要点：

- 第 11.2 章（会话绑定）：每个事务开始执行 ``set_config('app.current_org', <org>, true)``
  （等价 ``SET LOCAL``，作用域限事务，避免连接复用造成跨租户串读）。缺失该 GUC 时
  RLS 策略判定为 NULL ⇒ 查不到任何行、写不进去（fail-closed）。
- 第 11.1 章（角色分层）：应用连接使用 ``gbt_app``——**非表 owner、非超级用户**，且策略以
  ``FORCE ROW LEVEL SECURITY`` 创建，因此 RLS 必然生效；只有迁移用 ``gbt_owner``。
- 应用层同时按 ``organization_id`` 显式过滤（纵深防御），但**不以应用层过滤替代 RLS**。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings

ORG_GUC = "app.current_org"

_settings = get_settings()


def _make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, future=True)


#: 应用引擎（gbt_app，受 RLS 约束）
app_engine = _make_engine(_settings.database_url)
#: 迁移/种子引擎（gbt_owner，表 owner）
owner_engine = _make_engine(_settings.migration_database_url)

AppSession = sessionmaker(bind=app_engine, expire_on_commit=False, autoflush=False, future=True)
OwnerSession = sessionmaker(bind=owner_engine, expire_on_commit=False, autoflush=False, future=True)


def _bind_guc(session: Session, connection) -> None:
    org = session.info.get("current_org")
    if org is None:
        return
    # set_config 支持绑定参数；`SET LOCAL ... = %s` 在扩展协议下不被 PostgreSQL 接受。
    connection.execute(text("SELECT set_config(:key, :value, true)"), {"key": ORG_GUC, "value": str(org)})


@event.listens_for(Session, "after_begin")
def _bind_guc_on_begin(session: Session, transaction, connection) -> None:  # noqa: ARG001
    """每个事务开始时把当前组织写进 GUC（``app.current_org``）。

    注意这是 **Session** 事件而不是 Engine 事件：租户绑定属于"会话—事务"语义，而不是
    "连接建立"事件。写成 ``@event.listens_for(app_engine, "after_begin")`` 会在导入期直接
    抛 ``AttributeError: after_begin``——Engine 上没有这个事件。

    选 ``after_begin`` 而不是"建连接时 SET"的原因：GUC 必须逐事务绑定。
    连接池会把同一个物理连接交给下一个请求，若只在连接建立时设置一次，
    下一个请求就可能读到上一个租户的 GUC（跨租户串读）。
    """
    _bind_guc(session, connection)


def bind_org(session: Session, org_id: UUID | str) -> Session:
    """把租户上下文挂到会话上；下一个事务开始时会自动写入 GUC。"""
    session.info["current_org"] = str(org_id)
    return session


def current_org(session: Session) -> str | None:
    return session.info.get("current_org")


@contextmanager
def org_session(org_id: UUID | str) -> Iterator[Session]:
    """以指定组织上下文开启应用会话，正常退出即提交，异常回滚。"""
    session = AppSession()
    try:
        bind_org(session, org_id)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def owner_session(org_id: UUID | str | None = None) -> Iterator[Session]:
    """以表 owner 身份开启会话（仅用于种子数据、运维脚本与测试夹具）。

    注意：owner 在 ``FORCE ROW LEVEL SECURITY`` 下同样受策略约束，因此
    未传 ``org_id`` 时该会话在任何组织级表上读写都会失败——这正是期望行为。
    """
    session = OwnerSession()
    try:
        if org_id is not None:
            bind_org(session, org_id)
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()