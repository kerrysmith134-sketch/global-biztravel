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

输出：`ContractSamples.generated.swift` 与独立的 `VisitFollowUpContractSample.generated.swift`。大尺寸响应样本使用独立扩展，避免生成文件超过 SwiftLint 的文件/类型体积门槛。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts" / "openapi.json"
OUT_FILE = ROOT / "apps" / "ios" / "GlobalTravelOSTests" / "ContractSamples.generated.swift"
OUT_FOLLOW_UP_FILE = ROOT / "apps" / "ios" / "GlobalTravelOSTests" / "VisitFollowUpContractSample.generated.swift"

MAX_SAMPLE_LINES_PER_FILE = 420

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
    "VisitFollowUp": "VisitFollowUpRead",
    "VisitRecord": "VisitRecordRead",
    "TravelerProfile": "TravelerProfileRead",
    "AppNotification": "NotificationRead",
    "NotificationPreferenceRead": "NotificationPreferenceRead",
    "JobResult": "JobRead",
    # 版本比较：iOS 手写模型曾与真实响应完全漂移（base/compare vs base_plan_version_id/
    # target_plan_version_id、changes vs stops），加入契约样本后这类错误会在解码测试里直接失败。
    "PlanDiff": "PlanVersionDiffRead",
    "TripRiskCase": "TripRiskCaseRead",
    "TripRiskCaseEvent": "TripRiskCaseEventRead",
    "TripRiskReconciliation": "TripRiskReconciliationRead",
    "TripRiskTransition": "TripRiskTransitionRead",
    "MarketEvidenceIntelligenceReport": "MarketEvidenceIntelligenceRead",
    "MarketSourceVerificationPlan": "MarketSourceVerificationPlanRead",
    "MarketSourceVerificationPlanApplication": "MarketSourceVerificationPlanApplicationRead",
    "MarketClaimImpactReport": "MarketClaimImpactReportRead",
    "MarketClaimLineageHistory": "MarketClaimLineageRead",
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

        if "const" in node:
            return node["const"]

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
    follow_up_block: str | None = None
    for model, schema_name in sorted(TARGETS.items()):
        schema = schemas.get(schema_name)
        if schema is None:
            missing.append(f"{model} → {schema_name}")
            continue
        payload = generator.sample(schema)
        body = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        ident = model[0].lower() + model[1:]
        if model == "VisitFollowUp":
            indented_body = "\n".join(f"        {line}" for line in body.splitlines())
            follow_up_block = (
                "    static var visitFollowUp: String {\n"
                "        \"\"\"\n"
                f"{indented_body}\n"
                "        \"\"\"\n"
                "    }"
            )
        else:
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
    sample_chunks: list[list[str]] = []
    current_chunk: list[str] = []
    current_lines = 0
    for block in blocks:
        block_lines = len(block.splitlines()) + 1
        if current_chunk and current_lines + block_lines > MAX_SAMPLE_LINES_PER_FILE:
            sample_chunks.append(current_chunk)
            current_chunk = []
            current_lines = 0
        current_chunk.append(block)
        current_lines += block_lines
    if current_chunk:
        sample_chunks.append(current_chunk)

    generated_parts: set[Path] = set()
    for index, chunk in enumerate(sample_chunks):
        if index == 0:
            output_path = OUT_FILE
            file_header = header
            closing = "}\n"
        else:
            output_path = OUT_FILE.with_name(
                f"{OUT_FILE.stem}.part{index + 1}{OUT_FILE.suffix}"
            )
            file_header = (
                "// 自动生成，请勿手改：scripts/gen_ios_contract_samples.py\n"
                "// ContractSamples 分片；与主样本文件由同一 OpenAPI schema 生成。\n\n"
                "extension ContractSamples {\n"
            )
            closing = "}\n"
        output_path.write_text(
            file_header + "\n".join(chunk) + "\n" + closing,
            encoding="utf-8",
        )
        generated_parts.add(output_path)

    for stale_part in OUT_FILE.parent.glob(f"{OUT_FILE.stem}.part*{OUT_FILE.suffix}"):
        if stale_part not in generated_parts:
            stale_part.unlink()

    if follow_up_block is not None:
        follow_up_header = (
            "// 自动生成，请勿手改：scripts/gen_ios_contract_samples.py\n"
            "// 独立存放 VisitFollowUp 大响应样本，避免公共样本枚举超过 SwiftLint 体积门槛。\n\n"
            "extension ContractSamples {\n"
        )
        OUT_FOLLOW_UP_FILE.write_text(
            follow_up_header + follow_up_block + "\n}\n",
            encoding="utf-8",
        )
    elif OUT_FOLLOW_UP_FILE.exists():
        OUT_FOLLOW_UP_FILE.unlink()

    if missing:
        print("[FAIL] 契约里找不到这些 schema：", file=sys.stderr)
        for item in missing:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print(f"已生成 {written} 份契约样本 → {OUT_FILE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
