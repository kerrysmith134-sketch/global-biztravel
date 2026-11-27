"""Alembic 环境。

设计要点（对应设计文档 v2.0 第 12.2 章）：

- 迁移连接用 **gbt_owner**（表 owner），不是应用角色；应用角色 gbt_app 只跑 DML 且受 RLS 约束。
- 连接串来自 GBT_MIGRATION_DATABASE_URL，不落在 alembic.ini 里（红线 RL-05）。
- 迁移以**显式 DDL** 为准（可评审、可重复）；``autogenerate`` 只作辅助。env.py 仍接
  ``Base.metadata``，是为了让 ``alembic check`` 能发现"模型改了但没写迁移"的漂移。
- ``include_object`` 把**非本系统所有**与**数据库派生**的对象排除在比对之外（见下），
  否则 ``alembic check`` 会被 PostGIS 自带对象和派生列淹没，失去告警价值。
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import (
    Column,
    MetaData,
    PrimaryKeyConstraint,
    String,
    engine_from_config,
    inspect,
    pool,
    text,
)
from sqlalchemy.engine import Connection
from sqlalchemy.schema import Table

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app.models  # noqa: E402,F401  （副作用：把所有模型注册进 Base.metadata）
from app.config import get_settings  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.gis import register_gis_types  # noqa: E402

# 反射前注册 PostGIS 类型，避免 "Did not recognize type 'geography'" 噪音。
register_gis_types()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

settings = get_settings()
if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", settings.migration_database_url)

#: PostGIS 安装时自带的系统表，不由本系统迁移管理。
EXCLUDED_TABLES = frozenset({"spatial_ref_sys"})

#: 由数据库触发器维护的派生列，不进入 ORM，也不应参与漂移比对。
EXCLUDED_COLUMNS = frozenset({("places", "geog")})

#: 由迁移用原生 SQL 创建的索引（GIST 空间索引，SQLAlchemy 侧无声明）。
EXCLUDED_INDEXES = frozenset({"ix_places_geog"})

def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001, ARG001
    """决定某个对象是否参与 autogenerate / ``alembic check`` 比对。"""
    if type_ == "table":
        return name not in EXCLUDED_TABLES
    if type_ == "column":
        table_name = getattr(getattr(obj, "table", None), "name", None)
        return (table_name, name) not in EXCLUDED_COLUMNS
    if type_ in {"index", "unique_constraint", "check_constraint"}:
        return name not in EXCLUDED_INDEXES
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _ensure_revision_storage(connection: Connection) -> None:
    """Keep Alembic's revision column wide enough for semantic revision identifiers.

    Alembic defaults to VARCHAR(32), while this repository intentionally uses descriptive
    revision IDs (for example, 0052_recovery_reassessment_events). Bootstrap before
    Alembic's first version update so both fresh and existing PostgreSQL databases can
    traverse the full migration chain without rewriting already-published revision IDs.
    """
    version_table = config.get_main_option("version_table") or "alembic_version"
    schema = config.get_main_option("version_table_schema") or None
    inspector = inspect(connection)

    if not inspector.has_table(version_table, schema=schema):
        Table(
            version_table,
            MetaData(),
            Column("version_num", String(128), nullable=False),
            PrimaryKeyConstraint("version_num", name="alembic_version_pkc"),
            schema=schema,
        ).create(connection)
        return

    version_column = next(
        (
            column
            for column in inspector.get_columns(version_table, schema=schema)
            if column["name"] == "version_num"
        ),
        None,
    )
    if version_column is None:
        raise RuntimeError(f"{version_table} is missing its version_num column")

    current_length = getattr(version_column["type"], "length", None)
    if current_length is None or current_length >= 128:
        return
    if connection.dialect.name != "postgresql":
        raise RuntimeError(
            f"{version_table}.version_num must support at least 128 characters"
        )

    preparer = connection.dialect.identifier_preparer
    qualified_table = (
        f"{preparer.quote_schema(schema)}.{preparer.quote(version_table)}"
        if schema
        else preparer.quote(version_table)
    )
    connection.execute(
        text(
            f"ALTER TABLE {qualified_table} "
            f"ALTER COLUMN {preparer.quote('version_num')} TYPE VARCHAR(128)"
        )
    )


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        # Widen/create Alembic's own bookkeeping table before the first revision update.
        with connection.begin():
            _ensure_revision_storage(connection)
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_schemas=False,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()