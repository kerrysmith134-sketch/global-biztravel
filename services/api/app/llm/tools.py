"""工具契约目录（设计文档 §7.3）。

工具是 Agent 唯一允许触碰业务的入口：一律声明名称、入参 schema、权限域、是否需要人审、
是否幂等，并通过业务用例执行——**禁止 Agent 直连数据库**。当前目录覆盖已实现的主动式
建议动作与本次新增的纪要草稿；新增工具必须在这里登记，否则视为未授权能力。
"""

from __future__ import annotations

from typing import Any

TOOL_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "name": "trip.get_plan",
        "description": "读取某次出行的生效计划（只读）",
        "input_schema": {
            "type": "object",
            "required": ["trip_id"],
            "properties": {"trip_id": {"type": "string"}},
        },
        "scope": "trip:read",
        "requires_approval": False,
        "idempotent": True,
        "handler": "usecase:GetTripPlan",
    },
    {
        "name": "visa.prepare_checklist",
        "description": "生成签证/证件核对清单草稿（不提交）",
        "input_schema": {
            "type": "object",
            "required": ["trip_id", "countries"],
            "properties": {
                "trip_id": {"type": "string"},
                "countries": {"type": "array"},
            },
        },
        "scope": "trip:write",
        "requires_approval": True,
        "idempotent": True,
        "handler": "usecase:PrepareVisaChecklist",
    },
    {
        "name": "place.prepare_geocoding",
        "description": "为缺少坐标的地点生成补全任务草稿（不调用外部地理编码）",
        "input_schema": {
            "type": "object",
            "required": ["place_ids"],
            "properties": {"place_ids": {"type": "array"}},
        },
        "scope": "place:write",
        "requires_approval": True,
        "idempotent": True,
        "handler": "usecase:PrepareGeocoding",
    },
    {
        "name": "plan.prepare_adjustment",
        "description": "为未排项生成计划调整建议草稿（不生成新版本）",
        "input_schema": {
            "type": "object",
            "required": ["plan_version_id", "visit_ids"],
            "properties": {
                "plan_version_id": {"type": "string"},
                "visit_ids": {"type": "array"},
            },
        },
        "scope": "plan:write",
        "requires_approval": True,
        "idempotent": True,
        "handler": "usecase:PreparePlanAdjustment",
    },
    {
        "name": "visit.draft_minutes",
        "description": "根据现场记录起草会议纪要与跟进任务（草稿，不发送）",
        "input_schema": {
            "type": "object",
            "required": ["visit_id"],
            "properties": {"visit_id": {"type": "string"}},
        },
        "scope": "visit:write",
        "requires_approval": True,
        "idempotent": True,
        "handler": "usecase:DraftMeetingMinutes",
    },
)


def list_tools() -> list[dict[str, Any]]:
    return [dict(item) for item in TOOL_CATALOG]


__all__ = ["TOOL_CATALOG", "list_tools"]
