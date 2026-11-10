"""文档漂移检查脚本（check_docs.py）的行为测试。

TDD 目标：该门禁此前**零测试**——它自己管着"文档跟上代码"，却没有测试跟上它。
锁死的解析行为：ADR 文件/索引编号提取、迁移文件/文档清单编号提取、差异报告。
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_docs  # noqa: E402

# ---------------------------------------------------------------------------
# ADR 文件与索引编号提取（第 15~18 轮）
# ---------------------------------------------------------------------------


def test_round_15_adr_file_numbers(tmp_path):
    """轮 15：从目录里提取 ADR-XXXX 文件编号。"""
    (tmp_path / "ADR-0001-rls.md").write_text("x")
    (tmp_path / "ADR-0030-contract.md").write_text("x")
    check_docs.DECISIONS_DIR = tmp_path
    assert check_docs._adr_file_numbers() == {"0001", "0030"}


def test_round_16_adr_files_ignore_non_matching_names(tmp_path):
    """轮 16：README、错拼文件名、无 slug 的文件不算 ADR。"""
    (tmp_path / "README.md").write_text("index")
    (tmp_path / "ADR-broken.md").write_text("x")
    (tmp_path / "adr-0002-lowercase.md").write_text("x")  # 小写不算
    (tmp_path / "ADR-0003.md").write_text("x")  # 缺 slug 不算
    check_docs.DECISIONS_DIR = tmp_path
    assert check_docs._adr_file_numbers() == set()


def test_round_17_adr_index_numbers_parse_table_rows():
    """轮 17：从索引 README 的表格行提取编号。"""
    fake = (
        "# 索引\n\n"
        "| 编号 | 标题 | 状态 |\n|---|---|---|\n"
        "| ADR-0002 | RLS | 已接受 |\n"
        "| ADR-0030 | 契约 | 已接受 |\n"
        "\n约定：\n- 编号递增\n"
    )
    original = check_docs.DECISIONS_INDEX
    check_docs.DECISIONS_INDEX = Path("/tmp/fake-decisions-readme.md")
    check_docs.DECISIONS_INDEX.write_text(fake)
    try:
        assert check_docs._adr_index_numbers() == {"0002", "0030"}
    finally:
        check_docs.DECISIONS_INDEX = original


def test_round_18_adr_index_ignores_prose_mentions():
    """轮 18：约定正文里提到的 `ADR-xxxx`（非表格行）不算已登记。"""
    fake = (
        "# 索引\n\n| ADR-0002 | RLS | 已接受 |\n\n"
        "约定：\n- 新增 ADR 必须同步更新本索引\n- 被替代的写 Supersedes\n"
    )
    original = check_docs.DECISIONS_INDEX
    check_docs.DECISIONS_INDEX = Path("/tmp/fake-decisions-readme2.md")
    check_docs.DECISIONS_INDEX.write_text(fake)
    try:
        assert check_docs._adr_index_numbers() == {"0002"}
    finally:
        check_docs.DECISIONS_INDEX = original


# ---------------------------------------------------------------------------
# 迁移编号提取（第 19~21 轮）
# ---------------------------------------------------------------------------


def test_round_19_migration_file_numbers(tmp_path):
    """轮 19：从迁移目录提取 NNNN_slug.py 编号。"""
    (tmp_path / "0001_core_identity.py").write_text("x")
    (tmp_path / "0030_traveler_nationality.py").write_text("x")
    (tmp_path / "__init__.py").write_text("")
    (tmp_path / "conftest.py").write_text("")
    check_docs.MIGRATIONS_DIR = tmp_path
    assert check_docs._migration_file_numbers() == {"0001", "0030"}


def test_round_20_documented_migration_numbers_parse_section():
    """轮 20：从实施状态文档 §5 迁移清单提取编号。"""
    fake = (
        "# 实施状态\n\n"
        "## 4. 验证记录\n\n| x | y |\n\n"
        "## 5. 迁移清单（当前 head `0030`）\n\n"
        "| `0001_core_identity` | 身份 |\n"
        "| `0030_traveler_nationality` | 国籍 |\n\n"
        "## 6. 其他\n\n不该被解析：`9999_should_not_appear`\n"
    )
    original = check_docs.STATUS_DOC
    check_docs.STATUS_DOC = Path("/tmp/fake-status.md")
    check_docs.STATUS_DOC.write_text(fake)
    try:
        assert check_docs._documented_migration_numbers() == {"0001", "0030"}
    finally:
        check_docs.STATUS_DOC = original


def test_round_21_documented_migration_numbers_missing_header_is_empty():
    """轮 21：文档没有 §5 标题 → 空集（全量报缺失，而不是解析到无关编号）。"""
    fake = "# 实施状态\n\n没有迁移清单章节。\n"
    original = check_docs.STATUS_DOC
    check_docs.STATUS_DOC = Path("/tmp/fake-status2.md")
    check_docs.STATUS_DOC.write_text(fake)
    try:
        assert check_docs._documented_migration_numbers() == set()
    finally:
        check_docs.STATUS_DOC = original


# ---------------------------------------------------------------------------
# _report 差异报告（第 22~24 轮）
# ---------------------------------------------------------------------------


def test_round_22_report_flags_missing():
    """轮 22：目录有、文档没有 → 报「未登记」。"""
    problems = check_docs._report("测试", {"0001", "0002"}, {"0001"})
    assert len(problems) == 1
    assert "0002" in problems[0]
    assert "未登记" in problems[0]


def test_round_23_report_flags_extra():
    """轮 23：文档有、目录没有 → 报「目录里没有」。"""
    problems = check_docs._report("测试", {"0001"}, {"0001", "0099"})
    assert len(problems) == 1
    assert "0099" in problems[0]


def test_round_24_report_clean_is_silent():
    """轮 24：两边一致 → 无输出。"""
    assert check_docs._report("测试", {"0001"}, {"0001"}) == []


# ---------------------------------------------------------------------------
# main 集成（第 25 轮）
# ---------------------------------------------------------------------------


def test_round_25_main_reports_drift_and_exits_nonzero(tmp_path, monkeypatch, capsys):
    """轮 25：索引缺一条时 main 报错并退出 1——门禁的红线行为。"""
    real_dir = check_docs.DECISIONS_DIR
    real_index = check_docs.DECISIONS_INDEX
    (tmp_path / "ADR-0001-a.md").write_text("x")
    (tmp_path / "README.md").write_text("# 索引\n\n| 编号 | 标题 |\n|---|---|\n")
    monkeypatch.setattr(check_docs, "DECISIONS_DIR", tmp_path)
    monkeypatch.setattr(check_docs, "DECISIONS_INDEX", tmp_path / "README.md")
    monkeypatch.setattr(check_docs, "_migration_file_numbers", set)
    monkeypatch.setattr(check_docs, "_documented_migration_numbers", set)
    try:
        assert check_docs.main() == 1
        assert "0001" in capsys.readouterr().out
    finally:
        check_docs.DECISIONS_DIR = real_dir
        check_docs.DECISIONS_INDEX = real_index
