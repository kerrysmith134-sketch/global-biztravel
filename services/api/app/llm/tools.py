"""Agent 工具契约目录（设计文档 §7.3）。

目录只声明工具名称、输入 schema、权限域、审批要求和语义幂等性；目录声明不代表 handler
已经接通。可执行能力必须同时登记在 agent_tool_service 的显式执行器映射中，并通过该服务复用
租户业务用例、权限校验和 AgentRun 审计。未登记 handler 或审批链的目录项必须保持不可执行。
Agent 不得直连数据库。
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
        "name": "visa.get_checklist",
        "description": "读取本次出行已建立的签证案件与材料清单进度（只读，不推断签证要求）",
        "input_schema": {
            "type": "object",
            "required": ["trip_id"],
            "properties": {"trip_id": {"type": "string"}},
        },
        "scope": "trip:read",
        "requires_approval": False,
        "idempotent": True,
        "handler": "usecase:GetVisaChecklist",
    },
    {
        "name": "visa.prepare_checklist",
        "description": "用户点击确认后按行程目的国创建签证案件与材料清单（本地草稿，不提交申请）",
        "input_schema": {
            "type": "object",
            "required": ["trip_id"],
            "properties": {
                "trip_id": {"type": "string"},
                "countries": {"type": "array"},
            },
        },
        "scope": "trip:write",
        "requires_approval": False,
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
        "description": "用户点击确认后，根据该次拜访已生效的现场记录生成会议纪要草稿与跟进建议（不发送、不创建正式任务）",
        "input_schema": {
            "type": "object",
            "required": ["visit_id"],
            "properties": {
                "visit_id": {"type": "string"},
                "tone": {"type": "string", "enum": ["PROFESSIONAL", "CONCISE"]},
            },
        },
        "scope": "visit:write",
        "requires_approval": False,
        "idempotent": True,
        "handler": "usecase:DraftMeetingMinutes",
    },
)


def list_tools() -> list[dict[str, Any]]:
    return [dict(item) for item in TOOL_CATALOG]


__all__ = ["TOOL_CATALOG", "list_tools"]
