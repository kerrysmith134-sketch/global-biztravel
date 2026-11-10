"""种子夹具构造（seed_dev.build_fixture_rows）的行为测试。

TDD 目标：夹具日期动态化后（today+2/+3/+4）没有任何直接测试——iOS/导入
用例全靠它产出"相对关系固定、日期滚动"的数据。锁死的正是那些**相对关系**：
同窗去重、倒置时间窗、行结构完整。
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from seed_dev import FIXTURE_HEADERS, build_fixture_rows  # noqa: E402


def rows() -> list[list[object]]:
    return build_fixture_rows()


# 第 39 轮 -------------------------------------------------------------------


def test_round_39_row_shape_matches_headers():
    """每行字段数必须等于表头数——错位会让列识别整体漂移。"""
    for i, row in enumerate(rows(), start=2):
        assert len(row) == len(FIXTURE_HEADERS), f"第 {i} 行字段数 {len(row)} != 表头 {len(FIXTURE_HEADERS)}"


# 第 40 轮 -------------------------------------------------------------------


def test_round_40_dates_are_relative_to_today():
    """所有显式时间窗的日期部分 ∈ {today+2, today+3, today+4}——夹具不过期。"""
    allowed = {
        (date.today() + timedelta(days=offset)).isoformat() for offset in (2, 3, 4)
    }
    for i, row in enumerate(rows(), start=2):
        start, end = row[12], row[13]
        if not isinstance(start, str) or not isinstance(end, str):
            continue  # 第 2/3 行等故意留空
        assert start[:10] in allowed, f"第 {i} 行开始日期 {start[:10]} 不在 allowed：{allowed}"
        assert end[:10] in allowed, f"第 {i} 行结束日期 {end[:10]} 不在 allowed"


# 第 41 轮 -------------------------------------------------------------------


def test_round_41_duplicate_row_shares_window_with_original():
    """第 5 行（DUPLICATE_ROW 样本）必须与第 1 行同客户同时间窗——去重样本的语义。"""
    data = rows()
    first, duplicate = data[0], data[4]
    assert first[0] == duplicate[0], "客户名必须相同"
    assert first[2] == duplicate[2], "客户编号必须相同"
    assert first[12] == duplicate[12] and first[13] == duplicate[13], "时间窗必须相同"


# 第 42 轮 -------------------------------------------------------------------


def test_round_42_inverted_window_stays_inverted():
    """第 8 行（INVALID_TIME_WINDOW 样本）结束必须早于开始——倒置语义不能丢。"""
    row = rows()[7]
    start, end = str(row[12]), str(row[13])
    assert start[11:] == "16:00" and end[11:] == "14:00", f"倒置窗口被改掉了：{start} -> {end}"


# 第 43 轮 -------------------------------------------------------------------


def test_round_43_expected_problem_rows_are_stable():
    """各行被赋予的问题码语义稳定：空客户名行、三字母国家码行、可疑邮箱行都在。"""
    data = rows()
    assert data[5][0] is None, "第 6 行客户名应为 None（MISSING_CUSTOMER_NAME）"
    assert data[8][4] == "DEU", "第 9 行国家码应为三字母 DEU（INVALID_COUNTRY_CODE）"
    assert data[9][15].startswith("niamh.at."), "第 10 行邮箱格式可疑（INVALID_EMAIL）"


# 第 44 轮 -------------------------------------------------------------------


def test_round_44_fixture_row_count_and_distinct_customers():
    """10 行数据、8 类脏数据场景的载体——行数变化必须是有意为之。"""
    data = rows()
    assert len(data) == 10
    # 至少 7 个不同客户名（重复行复用第 1 行客户，两行留空）
    names = [r[0] for r in data if r[0]]
    assert len(set(names)) >= 7
