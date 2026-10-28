"""让 SQLAlchemy 认识 PostGIS 的 ``geography`` / ``geometry`` 类型。

为什么需要：``places.geog`` 由迁移以原生 DDL 建立，ORM 不映射它，但 SQLAlchemy 反射
数据库时仍会读到这一列。若方言不认识该类型，每次 ``alembic check`` / 测试建库都会打印
``SAWarning: Did not recognize type 'geography'``，长期会掩盖真实告警。

这里只注册一个类型描述符（不提供空间运算符），空间查询一律走原生 SQL + PostGIS 函数，
不把 PostGIS 的 Python 绑定引入运行时依赖（红线 RL-03：不引入未验证的第三方依赖）。
"""

from __future__ import annotations

from sqlalchemy import types
from sqlalchemy.dialects.postgresql.base import PGDialect


class Geography(types.UserDefinedType):
    """最小可用的 ``geography(<type>,<srid>)`` 描述符，仅用于反射与 DDL 类型匹配。"""

    cache_ok = True

    def __init__(self, geometry_type: str = "POINT", srid: int = 4326) -> None:
        self.geometry_type = geometry_type
        self.srid = srid

    def get_col_spec(self, **kw: object) -> str:  # noqa: ARG002
        return f"geography({self.geometry_type},{self.srid})"

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"Geography({self.geometry_type!r}, {self.srid})"


def register_gis_types() -> None:
    """幂等注册；可在 env.py、测试夹具、脚本中重复调用。"""
    PGDialect.ischema_names.setdefault("geography", Geography)
    PGDialect.ischema_names.setdefault("geometry", Geography)