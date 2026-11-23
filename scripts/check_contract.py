#!/usr/bin/env python
"""契约漂移检查（设计文档第 13.1 章第 5 条 / S6）。

以 `contracts/openapi.json` 为提交的真相源，重新从 FastAPI 生成一份并比较：

- 路径集合是否一致；
- schema 名称集合是否一致；
- 完整结构是否一致（字段/类型/必填项的漂移都会被抓到）。

用法：
    .venv/bin/python scripts/check_contract.py

退出码 0 = 一致；1 = 有漂移（并打印差异位置）。CI 里应先跑 `make openapi` 再跑本脚本。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))
sys.path.insert(0, str(ROOT / "packages" / "planning-engine"))

os.environ.setdefault("GBT_ENV", "local")

COMMITTED = ROOT / "contracts" / "openapi.json"


def _diff_keys(label: str, committed: set[str], generated: set[str]) -> list[str]:
    lines: list[str] = []
    missing = sorted(committed - generated)
    extra = sorted(generated - committed)
    if missing:
        lines.append(f"{label} 仅存在于提交契约：{missing}")
    if extra:
        lines.append(f"{label} 仅存在于代码：{extra}")
    return lines


def _value_preview(value: object) -> str:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return rendered if len(rendered) <= 240 else rendered[:237] + "..."


def _describe_differences(
    label: str,
    committed: object,
    generated: object,
    *,
    path: str = "$",
    limit: int = 30,
) -> list[str]:
    """Show exact JSON paths and values, keeping CI contract failures actionable."""
    if committed == generated:
        return []
    if isinstance(committed, dict) and isinstance(generated, dict):
        result: list[str] = []
        for key in sorted(set(committed) | set(generated)):
            child = f"{path}.{key}"
            if key not in committed:
                result.append(f"{label} {child}: 仅生成契约有 {_value_preview(generated[key])}")
            elif key not in generated:
                result.append(f"{label} {child}: 仅提交契约有 {_value_preview(committed[key])}")
            else:
                result.extend(
                    _describe_differences(
                        label, committed[key], generated[key], path=child, limit=limit
                    )
                )
            if len(result) >= limit:
                return result[:limit]
        return result
    if isinstance(committed, list) and isinstance(generated, list):
        if len(committed) != len(generated):
            return [
                f"{label} {path}: 数组长度提交={len(committed)} / 生成={len(generated)}"
            ]
        result = []
        for index, (left, right) in enumerate(zip(committed, generated, strict=False)):
            result.extend(
                _describe_differences(
                    label, left, right, path=f"{path}[{index}]", limit=limit
                )
            )
            if len(result) >= limit:
                return result[:limit]
        return result
    return [
        f"{label} {path}: 提交={_value_preview(committed)} / 生成={_value_preview(generated)}"
    ]


def main() -> int:
    if not COMMITTED.exists():
        print(f"缺少 {COMMITTED}，请先运行 make openapi")
        return 1

    from app.main import app

    generated = app.openapi()
    generated["openapi"] = "3.1.0"
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))

    problems: list[str] = []
    problems.extend(
        _diff_keys(
            "路径",
            set(committed.get("paths", {})),
            set(generated.get("paths", {})),
        )
    )
    problems.extend(
        _diff_keys(
            "schema",
            set(committed.get("components", {}).get("schemas", {})),
            set(generated.get("components", {}).get("schemas", {})),
        )
    )

    if not problems and committed != generated:
        # 路径与 schema 名称一致但内容变了：输出最小 JSON 路径差异和具体值。
        for path in sorted(set(committed.get("paths", {})) & set(generated.get("paths", {}))):
            problems.extend(
                _describe_differences(
                    f"路径 {path}",
                    committed["paths"][path],
                    generated["paths"][path],
                )
            )
        committed_schemas = committed.get("components", {}).get("schemas", {})
        generated_schemas = generated.get("components", {}).get("schemas", {})
        for name in sorted(set(committed_schemas) & set(generated_schemas)):
            problems.extend(
                _describe_differences(
                    f"schema {name}",
                    committed_schemas[name],
                    generated_schemas[name],
                )
            )

    if problems:
        print("契约漂移：")
        for problem in problems:
            print("  -", problem)
        print("请运行 make openapi 并提交更新后的 contracts/openapi.*")
        return 1

    print(
        f"契约一致：{len(generated.get('paths', {}))} 路径 / "
        f"{len(generated.get('components', {}).get('schemas', {}))} schema"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
