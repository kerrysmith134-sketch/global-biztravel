#!/usr/bin/env python
"""旋转 PII 字段加密密钥（设计文档 §11.4 / ADR-0009）。

对 ``customers`` 联系人三字段与 ``visit_records.notes`` 做重加密。默认跳过明文；
显式 ``--include-plaintext`` 才会把旧明文也加密。

用法：
    .venv/bin/python scripts/rotate_field_key.py \
        --old-key "$GBT_OLD_FIELD_KEY" --new-key "$GBT_NEW_FIELD_KEY" [--include-plaintext]

密钥只从命令行/环境读取，脚本不打印密钥。执行前请先备份数据库。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.db import owner_engine  # noqa: E402
from app.security.key_rotation import rotate_value  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

TARGETS = (
    ("customers", ("primary_contact_name", "primary_contact_email", "primary_contact_phone")),
    ("visit_records", ("notes",)),
)


def rotate_table(connection, table: str, columns: tuple[str, ...], old: str | None, new: str, include_plain: bool) -> dict[str, int]:
    counts: dict[str, int] = {}
    rows = connection.execute(select("*").select_from(text(table))).mappings().all()
    for row in rows:
        updates: dict[str, str] = {}
        for column in columns:
            rotated, status = rotate_value(
                row[column], old_key=old, new_key=new, encrypt_plaintext=include_plain
            )
            counts[status] = counts.get(status, 0) + 1
            if rotated != row[column]:
                updates[column] = rotated
        if updates:
            assignments = ", ".join(f"{column} = :{column}" for column in updates)
            connection.execute(
                text(f"UPDATE {table} SET {assignments} WHERE id = :id"),
                {**updates, "id": row["id"]},
            )
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="旋转字段加密密钥")
    parser.add_argument("--old-key", default=os.environ.get("GBT_OLD_FIELD_KEY"))
    parser.add_argument("--new-key", default=os.environ.get("GBT_NEW_FIELD_KEY"))
    parser.add_argument("--include-plaintext", action="store_true")
    args = parser.parse_args(argv)
    if not args.new_key:
        print("缺少 --new-key / GBT_NEW_FIELD_KEY", file=sys.stderr)
        return 2

    with owner_engine.begin() as connection:
        for table, columns in TARGETS:
            counts = rotate_table(
                connection, table, columns, args.old_key, args.new_key, args.include_plaintext
            )
            print(f"{table}: {counts}")
    print("轮换完成。请更新 GBT_FIELD_ENCRYPTION_KEY 并重启服务。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
