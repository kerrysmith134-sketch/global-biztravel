#!/usr/bin/env python
"""文档一致性检查：ADR 索引、迁移清单与代码目录必须互相对得上。

两个清单历史上都出现过"代码加了、文档忘了"的漂移（ADR 索引曾落后 12 条，
靠人工核对才发现）。这里把它们变成可执行的门禁：

1. ``docs/decisions/README.md`` 的索引编号 == ``docs/decisions/ADR-*.md`` 文件编号；
2. ``docs/review/implementation-status.md`` §5 迁移清单 == ``services/api/migrations/versions/*.py``。

只比编号集合：标题措辞允许索引用简称（人工可读性优先），编号是唯一硬约束。

用法：
    .venv/bin/python scripts/check_docs.py

退出码 0 = 一致；1 = 有漂移（打印缺失/多余编号）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DECISIONS_DIR = ROOT / "docs" / "decisions"
DECISIONS_INDEX = DECISIONS_DIR / "README.md"
STATUS_DOC = ROOT / "docs" / "review" / "implementation-status.md"
MIGRATIONS_DIR = ROOT / "services" / "api" / "migrations" / "versions"

TEXT_SUFFIXES = {
    ".md", ".py", ".swift", ".yml", ".yaml", ".json", ".toml",
    ".sh", ".sql", ".plist", ".pbxproj", ".txt",
}
SKIPPED_PARTS = {".git", ".venv", "node_modules", "DerivedData"}

ADR_FILE_RE = re.compile(r"ADR-(\d{4})-[a-z0-9-]+\.md$")
ADR_INDEX_RE = re.compile(r"^\|\s*ADR-(\d{4})\s*\|", re.MULTILINE)


def _adr_file_numbers() -> set[str]:
    if not DECISIONS_DIR.is_dir():
        return set()
    numbers: set[str] = set()
    for path in DECISIONS_DIR.glob("ADR-*.md"):
        match = ADR_FILE_RE.search(path.name)
        if match:
            numbers.add(match.group(1))
    return numbers


def _adr_index_numbers() -> set[str]:
    if not DECISIONS_INDEX.is_file():
        return set()
    return set(ADR_INDEX_RE.findall(DECISIONS_INDEX.read_text(encoding="utf-8")))


def _migration_file_numbers() -> set[str]:
    if not MIGRATIONS_DIR.is_dir():
        return set()
    numbers: set[str] = set()
    for path in MIGRATIONS_DIR.glob("*.py"):
        match = re.match(r"(\d{4})_", path.name)
        if match:
            numbers.add(match.group(1))
    return numbers


def _documented_migration_numbers() -> set[str]:
    """解析 implementation-status 的迁移清单代码块（形如 ``0001_core_identity``）。"""
    if not STATUS_DOC.is_file():
        return set()
    text = STATUS_DOC.read_text(encoding="utf-8")
    start = text.find("## 5. 迁移清单")
    if start < 0:
        return set()
    end = text.find("\n## ", start + 1)
    section = text[start:end if end > 0 else len(text)]
    return set(re.findall(r"\b(\d{4})_[a-z0-9_]+", section))


def _merge_conflict_marker_problems() -> list[str]:
    """Fail CI when an unresolved Git conflict block is committed to a text file."""
    problems: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        if any(part in SKIPPED_PARTS for part in path.parts):
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue

        opening_line: int | None = None
        has_separator = False
        for line_number, line in enumerate(lines, start=1):
            if line.startswith("<<<<<<< "):
                if opening_line is not None:
                    problems.append(
                        f"{path.relative_to(ROOT)}:{line_number}：检测到嵌套冲突标记"
                    )
                opening_line = line_number
                has_separator = False
            elif opening_line is not None and line.strip() == "=======":
                has_separator = True
            elif line.startswith(">>>>>>> "):
                if opening_line is None or not has_separator:
                    problems.append(
                        f"{path.relative_to(ROOT)}:{line_number}：存在未配对的冲突结束标记"
                    )
                else:
                    problems.append(
                        f"{path.relative_to(ROOT)}:{opening_line}-{line_number}：存在未解决的 Git 合并冲突"
                    )
                opening_line = None
                has_separator = False

        if opening_line is not None:
            problems.append(
                f"{path.relative_to(ROOT)}:{opening_line}：冲突块未闭合"
            )
    return problems


def _report(label: str, files: set[str], documented: set[str]) -> list[str]:
    problems: list[str] = []
    missing = sorted(files - documented)
    extra = sorted(documented - files)
    if missing:
        problems.append(f"{label}：目录里有但文档未登记 → {missing}")
    if extra:
        problems.append(f"{label}：文档登记了但目录里没有 → {extra}")
    return problems


def main() -> int:
    problems = [
        *_report("ADR 索引", _adr_file_numbers(), _adr_index_numbers()),
        *_report("迁移清单", _migration_file_numbers(), _documented_migration_numbers()),
        *_merge_conflict_marker_problems(),
    ]
    if problems:
        print("[FAIL] 文档与代码不一致：")
        for line in problems:
            print(f"  - {line}")
        print("\n修复：更新 docs/decisions/README.md 索引 / implementation-status.md §5 清单。")
        return 1
    print(
        "文档一致：ADR 索引 "
        f"{len(_adr_file_numbers())} 条；迁移清单 {len(_migration_file_numbers())} 条。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
