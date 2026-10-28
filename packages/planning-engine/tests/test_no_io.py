"""架构不变量测试（AST 静态扫描）：规划引擎必须是**纯计算包**。

这是设计文档第 6.1 节"引擎无 IO、不碰数据库、不与用户交互、不做外部预订"和
第 6.3 节"独立验证器不复用求解器逻辑"的可执行版本。之所以用 AST 而不是人工约定，
是因为这类约束一旦退化成注释就一定会被违反。

规则清单（每条都对应一条真实风险）：
1. 模块级导入白名单 —— 引擎不得引入 IO/网络/随机/并发依赖；
2. `ortools` **只能函数级懒导入** —— 保证未安装 OR-Tools 的环境仍能导入本包；
3. 任何层级都禁止数据库/HTTP 客户端（`sqlalchemy`、`httpx`、`requests` …）；
4. 禁止 `open(` —— 引擎不读文件；
5. 禁止读时钟（`datetime.now/utcnow/today/fromtimestamp`、`time.*`）—— 结果必须可重放；
6. `validator.py` 不得导入 `travel_planner.scheduling` —— 避免"自己校验自己"；
7. 扫描规模下限 —— 防止目录改名后测试静默变成空跑。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import NamedTuple

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "travel_planner"

#: 模块级导入的**根模块**白名单：纯计算、纯数据、纯类型。
ALLOWED_TOP_LEVEL_ROOTS = frozenset(
    {
        "__future__",
        "dataclasses",
        "datetime",
        "enum",
        "hashlib",
        "json",
        "math",
        "pydantic",
        "travel_planner",
        "uuid",
        "zoneinfo",
    }
)

#: 任何层级都禁止的依赖：IO / 网络 / 随机 / 并发 / 进程。
BANNED_ANYWHERE_ROOTS = frozenset(
    {
        "asyncio",
        "ftplib",
        "glob",
        "http",
        "httpx",
        "io",
        "logging",
        "multiprocessing",
        "os",
        "pathlib",
        "random",
        "requests",
        "secrets",
        "shutil",
        "socket",
        "socketserver",
        "sqlite3",
        "sqlalchemy",
        "sqlmodel",
        "subprocess",
        "tempfile",
        "threading",
        "time",
        "urllib",
    }
)

#: 禁止读取当前时刻的属性：它们会让"相同输入得到不同输出"。
#: 注意 `time(0, 0, 0)` 是 `datetime.time` 的**构造调用**，不是模块属性访问，不受影响。
CLOCK_ATTRIBUTES = frozenset(
    {
        "now",
        "utcnow",
        "today",
        "fromtimestamp",
        "utcfromtimestamp",
        "time",
        "monotonic",
        "perf_counter",
        "process_time",
        "sleep",
        "localtime",
        "gmtime",
        "strftime",
        "mktime",
        "clock",
    }
)

#: 读取时钟的**根名字**：`datetime`（含 `datetime.datetime.now`）与 `date`。
CLOCK_ROOTS = frozenset({"datetime", "date"})

MIN_SCANNED_FILES = 10


class Violation(NamedTuple):
    path: str
    lineno: int
    rule: str
    detail: str

    def __str__(self) -> str:  # pragma: no cover - 仅用于断言失败时的人类可读输出
        return f"{self.path}:{self.lineno} [{self.rule}] {self.detail}"


def _module_files() -> list[Path]:
    return sorted(PACKAGE_ROOT.rglob("*.py"))


def _root_of(module: str | None) -> str:
    if not module:
        return ""
    return module.split(".", 1)[0]


def _iter_imports(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield node, alias.name
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` / `from .mod import y` 的相对导入根是当前包。
            module = node.module if node.level == 0 else f"travel_planner.{node.module or ''}"
            yield node, module


def _scan(path: Path) -> list[Violation]:
    # 被扫描的文件可能位于包外（自证夹具用 tmp_path 造样本），此时退回绝对路径，
    # 只影响诊断输出里的路径前缀，不影响规则判定。
    try:
        relative = str(path.relative_to(PACKAGE_ROOT.parent))
    except ValueError:
        relative = str(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[Violation] = []

    for node, module in _iter_imports(tree):
        root = _root_of(module)
        if root in BANNED_ANYWHERE_ROOTS:
            violations.append(
                Violation(relative, node.lineno, "BANNED_DEPENDENCY", f"import {module}")
            )
            continue
        is_top_level = node.col_offset == 0
        if is_top_level and root not in ALLOWED_TOP_LEVEL_ROOTS:
            violations.append(
                Violation(
                    relative,
                    node.lineno,
                    "TOP_LEVEL_IMPORT_NOT_ALLOWED",
                    f"import {module}（根模块 {root!r} 不在白名单内）",
                )
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id == "open":
                violations.append(
                    Violation(relative, node.lineno, "NO_IO", "call to open()")
                )
            if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                if func.value.id in CLOCK_ROOTS and func.attr in CLOCK_ATTRIBUTES:
                    violations.append(
                        Violation(
                            relative,
                            node.lineno,
                            "NO_CLOCK",
                            f"{func.value.id}.{func.attr}()",
                        )
                    )

    if path.name == "validator.py":
        for node, module in _iter_imports(tree):
            if module.startswith("travel_planner.scheduling"):
                violations.append(
                    Violation(
                        relative,
                        node.lineno,
                        "VALIDATOR_MUST_BE_INDEPENDENT",
                        f"validator imports {module}",
                    )
                )

    return violations


def test_scan_covers_the_whole_package():
    files = _module_files()
    assert len(files) >= MIN_SCANNED_FILES, [str(p) for p in files]
    assert PACKAGE_ROOT / "validator.py" in files
    assert PACKAGE_ROOT / "solvers" / "ortools_solver.py" in files


def test_no_violations_in_engine_package():
    violations = [v for path in _module_files() for v in _scan(path)]
    assert not violations, "\n".join(str(v) for v in violations)


@pytest.mark.parametrize(
    "source,expected_rule",
    [
        ("import sqlalchemy\n", "BANNED_DEPENDENCY"),
        ("import httpx\n", "BANNED_DEPENDENCY"),
        ("import os\n", "BANNED_DEPENDENCY"),
        ("import random\n", "BANNED_DEPENDENCY"),
        ("import time\n", "BANNED_DEPENDENCY"),
        ("import logging\n", "BANNED_DEPENDENCY"),
        ("import urllib.request\n", "BANNED_DEPENDENCY"),
        ("import ortools\n", "TOP_LEVEL_IMPORT_NOT_ALLOWED"),
        ("import numpy\n", "TOP_LEVEL_IMPORT_NOT_ALLOWED"),
        ("from flask import Flask\n", "TOP_LEVEL_IMPORT_NOT_ALLOWED"),
        ("def f():\n    return open('x')\n", "NO_IO"),
        ("from datetime import datetime\n\ndatetime.now()\n", "NO_CLOCK"),
        ("from datetime import datetime\n\ndatetime.utcnow()\n", "NO_CLOCK"),
    ],
)
def test_scanner_detects_each_banned_pattern(tmp_path, source, expected_rule):
    """用夹具源码证明扫描器本身有效：每条规则都至少能被触发一次。"""
    sample = tmp_path / "sample.py"
    sample.write_text(source, encoding="utf-8")
    monkey = _scan(sample)
    assert [v.rule for v in monkey] == [expected_rule]


def test_scanner_allows_the_legitimate_patterns(tmp_path):
    """反向夹具：合法写法（函数级懒导入、`datetime.time` 构造）必须零告警。"""
    sample = tmp_path / "sample.py"
    sample.write_text(
        "from __future__ import annotations\n"
        "from datetime import datetime, time\n"
        "from uuid import UUID\n"
        "\n"
        "import travel_planner.contracts\n"
        "\n"
        "\n"
        "def lazy() -> int:\n"
        "    from ortools.constraint_solver import pywrapcp\n"
        "\n"
        "    midnight = datetime(2026, 11, 9, tzinfo=None).replace(\n"
        "        hour=time(0, 0, 0).hour\n"
        "    )\n"
        "    return pywrapcp.__name__ == 'x' and midnight.hour\n",
        encoding="utf-8",
    )

    assert _scan(sample) == []


def test_ortools_is_lazy_imported_only():
    """精确锁定 `ortools` 的导入位置：只能出现在函数体内。"""
    path = PACKAGE_ROOT / "solvers" / "ortools_solver.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    module_level = [
        (node.lineno, module)
        for node, module in _iter_imports(tree)
        if _root_of(module) == "ortools" and node.col_offset == 0
    ]
    function_level = [
        (node.lineno, module)
        for node, module in _iter_imports(tree)
        if _root_of(module) == "ortools" and node.col_offset > 0
    ]

    assert module_level == []
    assert function_level, "ortools 应至少在函数内被懒导入一次"