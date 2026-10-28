#!/usr/bin/env python
"""从 FastAPI 应用导出 OpenAPI 3.1 契约到 contracts/openapi.yaml。

契约是唯一真相源（设计文档 v2.0 第 13.1 章第 5 条）：
iOS 客户端与后端都以本文件为准，改动后必须重新导出并提交。

用法：
    .venv/bin/python scripts/export_openapi.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))

os.environ.setdefault("GBT_ENV", "local")


def main() -> int:
    from app.main import app  # 延迟导入：避免导出脚本在任何 DB 依赖上失败

    spec = app.openapi()
    spec["openapi"] = "3.1.0"

    out_yaml = ROOT / "contracts" / "openapi.yaml"
    out_json = ROOT / "contracts" / "openapi.json"
    out_yaml.parent.mkdir(parents=True, exist_ok=True)

    try:
        import yaml  # type: ignore

        out_yaml.write_text(yaml.safe_dump(spec, allow_unicode=True, sort_keys=False), encoding="utf-8")
    except ImportError:
        # 不引入额外依赖：无 PyYAML 时同时落 json，yaml 用合法 JSON（YAML 1.2 是 JSON 超集）
        out_yaml.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    out_json.write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

    paths = len(spec.get("paths", {}))
    schemas = len(spec.get("components", {}).get("schemas", {}))
    print(f"OpenAPI 导出完成：{paths} 个路径、{schemas} 个 schema")
    print(f"  - {out_yaml}")
    print(f"  - {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())