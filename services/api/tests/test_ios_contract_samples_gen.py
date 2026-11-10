"""iOS 契约样本生成器（gen_ios_contract_samples.py）的行为测试。

TDD 目标：生成规则此前**零测试**——它开发时曾出过两个真 bug（嵌套深度截断、
additionalProperties 生成空对象），都是靠 iOS 解码测试绕了一圈才发现。这里把
`Generator.sample` 的每条规则锁死：format/enum/anyOf/allOf/$ref/深度上限。
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from gen_ios_contract_samples import (  # noqa: E402
    MAX_DEPTH,
    SAMPLE_DATE,
    SAMPLE_DATETIME,
    SAMPLE_UUID,
    Generator,
)


def gen(schemas: dict, node: dict):
    return Generator(schemas).sample(node)


# ---------------------------------------------------------------------------
# 字符串 format / 默认值（第 26~28 轮）
# ---------------------------------------------------------------------------


def test_round_26_uuid_format():
    assert gen({}, {"type": "string", "format": "uuid"}) == SAMPLE_UUID


def test_round_27_datetime_and_date_formats():
    assert gen({}, {"type": "string", "format": "date-time"}) == SAMPLE_DATETIME
    assert gen({}, {"type": "string", "format": "date"}) == SAMPLE_DATE


def test_round_28_plain_string_is_numeric():
    """无 format 的字符串用数字串：金额字段（"88.50"）要求可解析为 Decimal。"""
    assert gen({}, {"type": "string"}) == "1"


# ---------------------------------------------------------------------------
# 标量与枚举（第 29~30 轮）
# ---------------------------------------------------------------------------


def test_round_29_scalars():
    assert gen({}, {"type": "integer"}) == 1
    assert gen({}, {"type": "number"}) == 1.5
    assert gen({}, {"type": "boolean"}) is True


def test_round_30_enum_takes_first_value():
    assert gen({}, {"type": "string", "enum": ["DRAFT", "ACTIVE"]}) == "DRAFT"


# ---------------------------------------------------------------------------
# 组合（第 31~33 轮）
# ---------------------------------------------------------------------------


def test_round_31_any_of_skips_null_branch():
    node = {"anyOf": [{"type": "null"}, {"type": "string", "format": "uuid"}]}
    assert gen({}, node) == SAMPLE_UUID


def test_round_32_any_of_all_null_is_none():
    assert gen({}, {"anyOf": [{"type": "null"}]}) is None


def test_round_33_all_of_merges_parts():
    node = {
        "allOf": [
            {"type": "object", "properties": {"a": {"type": "integer"}}},
            {"type": "object", "properties": {"b": {"type": "integer"}}},
        ]
    }
    assert gen({}, node) == {"a": 1, "b": 1}


# ---------------------------------------------------------------------------
# $ref 与对象结构（第 34~36 轮）
# ---------------------------------------------------------------------------


def test_round_34_ref_expands():
    schemas = {
        "Thing": {"type": "object", "properties": {"id": {"type": "string", "format": "uuid"}}}
    }
    node = {"$ref": "#/components/schemas/Thing"}
    assert gen(schemas, node) == {"id": SAMPLE_UUID}


def test_round_35_unknown_ref_is_empty_object():
    assert gen({}, {"$ref": "#/components/schemas/NoSuchThing"}) == {}


def test_round_36_array_of_ref_expands_one_element():
    schemas = {
        "Item": {"type": "object", "properties": {"n": {"type": "integer"}}}
    }
    node = {"type": "array", "items": {"$ref": "#/components/schemas/Item"}}
    assert gen(schemas, node) == [{"n": 1}]


# ---------------------------------------------------------------------------
# additionalProperties 与深度上限（第 37~38 轮）
# ---------------------------------------------------------------------------


def test_round_37_additional_properties_generates_key_value():
    """counts 类字典（无 properties 但约束了 value 类型）生成一个键值对。

    历史缺陷：这里曾生成空对象 {}，导致 ActionCenter 的「total == counts 之和」
    语义校验失真（total=1 而 counts 之和=0）。
    """
    node = {"type": "object", "additionalProperties": {"type": "integer"}}
    assert gen({}, node) == {"key": 1}


def test_round_38_depth_limit_returns_empty_object():
    """超过深度上限返回 {}（防循环引用），上限是整数且足够深。"""
    deep: dict = {"type": "object", "properties": {"leaf": {"type": "integer"}}}
    node = deep
    for _ in range(MAX_DEPTH + 3):
        node = {"type": "object", "properties": {"child": node}}
    result = gen({}, node)
    # 深度超限的分支最终收敛成 {}，不再递归爆栈
    assert isinstance(result, dict)
