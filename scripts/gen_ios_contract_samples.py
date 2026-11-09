#!/usr/bin/env python
"""从 OpenAPI 契约生成 iOS 解码样本（S6：前后端 schema 漂移的运行时门禁）。

为什么需要它：iOS 的 `ModelDecodingTests` 用的是**手写 JSON**——样本怎么写、测试就怎么过，
后端契约改了字段名/类型，手写样本不会跟着变，漂移只能靠人肉发现。这里改由 `contracts/openapi.json`
（唯一真相源）生成样本，Swift 测试直接拿它解码：契约一变，iOS 解码测试立刻红。

生成规则（只求"类型合法、字段齐全"，不追求业务语义）：

- properties 全部填值（含 optional）：这样模型无论用 decode 还是 decodeIfPresent 都能过，
  失败只说明真实的字段名/类型漂移；
- 可空字段填非空值（类型检查更严）；
- 字符串按 ``format`` 与 ``enum`` 取合法值（uuid / date-time / date 有固定样例）；
- 数组给 1 个递归元素（空数组会掩盖结构问题）；
- ``$ref`` 递归展开，超过深度上限时数组给空、对象给最小字段（防循环引用）。

用法::

    .venv/bin/python scripts/gen_ios_contract_samples.py

输出：``apps/ios/GlobalTravelOSTests/ContractSamples/<SchemaName>.json``（生成产物，入库便于
Xcode 测试直接读；`make ios-contract-samples` 重新生成，`make ios-contract-check` 校验是否过期）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "openapi.json"
OUT_FILE = ROOT / "apps" / "ios" / "GlobalTravelOSTests" / "ContractSamples.generated.swift"

#: 生成哪些 schema 的样本：与 iOS 解码测试覆盖的模型一一对应（模型名 → 契约 schema）。
TARGETS: dict[str, str] = {
    "Trip": "TripRead",
    "PlanVersionDetail": "PlanVersionDetailRead",
    "VisitBriefing": "VisitBriefingRead",
    "BudgetSummary": "BudgetSummaryRead",
    "ActionCenter": "ActionCenterRead",
    "NotificationFeed": "NotificationFeedRead",
    "CrmSyncJob": "CrmSyncJobRead",
    "TripMemory": "TripMemoryRead",
    "MinutesDraft": "MinutesDraftRead",
    "TripOutcome": "TripOutcomeRead",
    "LocalGuide": "LocalGuideRead",
    "AssistantConversationDetail": "AssistantConversationDetailRead",
    "SuggestionFeed": "SuggestionFeedRead",
    # ---- 第二批：覆盖更多既有读模型（只验证"契约样本能解码"，字段级断言在各模型测试） ----
    "PlanVersion": "PlanVersionRead",
    "Customer": "CustomerRead",
    "Place": "PlaceRead",
    "VisaCase": "VisaCaseRead",
    "EntryAssessment": "EntryAssessmentRead",
    "TripExpense": "ExpenseRead",
    "ExpenseReport": "ExpenseReportRead",
    "ImportParseResult": "ImportParseRead",
    "SyncSummary": "SyncSummaryRead",
    "VisitRecord": "VisitRecordRead",
    "TravelerProfile": "TravelerProfileRead",
    "AppNotification": "NotificationRead",
    "NotificationPreferenceRead": "NotificationPreferenceRead",
    "JobResult": "JobRead",
}

SAMPLE_UUID = "11111111-1111-1111-1111-111111111111"
SAMPLE_DATETIME = "2026-11-09T09:00:00+00:00"
SAMPLE_DATE = "2026-11-09"
#: 嵌套上限：ActionCenter 这类 Read→array→$ref→array→$ref→properties 已需 6 层，
#: 给足余量；超出只用空对象兜底（契约深层结构本身不该无限嵌套）。
MAX_DEPTH = 14


class Generator:
    def __init__(self, schemas: dict[str, Any]) -> None:
        self.schemas = schemas

    def sample(self, node: dict[str, Any], depth: int = 0) -> Any:
        if depth > MAX_DEPTH:
            return {}

        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            name = ref.rsplit("/", 1)[-1]
            target = self.schemas.get(name)
            if target is None:
                return {}
            return self.sample(target, depth + 1)

        if "anyOf" in node:
            # 取第一个非 null 分支（可空字段填真值，类型检查更严）。
            branches = [b for b in node["anyOf"] if b.get("type") != "null"]
            return self.sample(branches[0], depth) if branches else None
        if "oneOf" in node:
            return self.sample(node["oneOf"][0], depth)
        if "allOf" in node:
            merged: dict[str, Any] = {}
            for part in node["allOf"]:
                value = self.sample(part, depth)
                if isinstance(value, dict):
                    merged.update(value)
            return merged

        enum = node.get("enum")
        if enum:
            return enum[0]

        node_type = node.get("type")
        if node_type == "object" or "properties" in node:
            properties = node.get("properties") or {}
            if properties:
                return {
                    key: self.sample(value, depth + 1)
                    for key, value in properties.items()
                }
            # 无显式 properties 但约束了 value 类型的字典（如 counts: {"某类": 1}）：
            # 生成一个键值对，避免空对象让"total == counts 之和"这类语义校验失真。
            extra = node.get("additionalProperties")
            if isinstance(extra, dict):
                return {"key": self.sample(extra, depth + 1)}
            return {}
        if node_type == "array":
            items = node.get("items") or {}
            return [self.sample(items, depth + 1)]
        if node_type == "integer":
            return 1
        if node_type == "number":
            return 1.5
        if node_type == "boolean":
            return True
        if node_type == "string":
            fmt = node.get("format")
            if fmt == "uuid":
                return SAMPLE_UUID
            if fmt == "date-time":
                return SAMPLE_DATETIME
            if fmt == "date":
                return SAMPLE_DATE
            # 无 format 的字符串用数字串：金额字段（如 "88.50"）要求可解析为 Decimal，
            # 普通文本字段用 "1" 同样合法。
            return "1"
        if node_type == "null":
            return None
        return None


def main() -> int:
    if not CONTRACT.is_file():
        print(f"缺少契约文件：{CONTRACT}（先跑 make openapi）", file=sys.stderr)
        return 1
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    schemas = contract["components"]["schemas"]
    generator = Generator(schemas)

    written = 0
    missing: list[str] = []
    blocks: list[str] = []
    for model, schema_name in sorted(TARGETS.items()):
        schema = schemas.get(schema_name)
        if schema is None:
            missing.append(f"{model} → {schema_name}")
            continue
        payload = generator.sample(schema)
        body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        ident = model[0].lower() + model[1:]
        blocks.append(f'    static let {ident} = """\n{body}\n"""')
        written += 1

    header = (
        "// 自动生成，请勿手改：scripts/gen_ios_contract_samples.py\n"
        "// 数据源：contracts/openapi.json（S6 契约漂移门禁；make ios-contract-samples 重新生成）\n"
        "//\n"
        "// 这些样本让 ModelDecodingTests 之外再有一层“契约→解码”检查：\n"
        "// 后端 schema 改了字段名/类型，对应样本随之变化，解码测试会立刻失败。\n\n"
        "enum ContractSamples {\n"
    )
    OUT_FILE.write_text(header + "\n".join(blocks) + "\n}\n", encoding="utf-8")

    if missing:
        print("[FAIL] 契约里找不到这些 schema：", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(f"已生成 {written} 份契约样本 → {OUT_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
