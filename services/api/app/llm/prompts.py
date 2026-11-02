"""版本化 prompt 模板与输出 schema。

设计文档 v2.0 第 7.4 章：prompt 必须带模板 id 与版本，每次调用写入 ``llm_invocations``，
从而能回答"这条产出来自哪版 prompt"。模板改动一律新增版本，不原地改写。
"""

from __future__ import annotations

from typing import Any

MEETING_MINUTES_TEMPLATE_ID = "meeting_minutes"
MEETING_MINUTES_PROMPT_VERSION = "v1"

MEETING_MINUTES_SYSTEM = (
    "你是企业差旅助手。根据现场拜访记录起草会议纪要，只输出 JSON。"
    "summary 用 2-4 句客观描述，decisions 只写客户当场确认的结论（没有就留空数组），"
    "follow_up_tasks 每条都必须有 title；不要编造未在输入中出现的事实。"
)

MEETING_MINUTES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["title", "summary", "decisions", "follow_up_tasks"],
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "decisions": {"type": "array"},
        "follow_up_tasks": {"type": "array"},
    },
}

TRIP_INTAKE_TEMPLATE_ID = "trip_intake"
TRIP_INTAKE_PROMPT_VERSION = "v1"

TRIP_INTAKE_SYSTEM = (
    "你是企业差旅助手。判断用户消息是否在请求创建一次出差，只输出 JSON。"
    "has_trip_request 为 true 时必须给出：countries（ISO 两位字母大写数组）、"
    "days（正整数天数）或 start_date/end_date（YYYY-MM-DD）之一、name（中文短名称）。"
    "目的国家或天数/日期在消息中未出现时不要编造，让对应字段缺失；"
    "不涉及创建出差的消息输出 has_trip_request=false。"
)

TRIP_INTAKE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["has_trip_request"],
    "properties": {
        "has_trip_request": {"type": "boolean"},
        "name": {"type": "string"},
        "countries": {"type": "array"},
        "days": {"type": "integer"},
        "start_date": {"type": "string"},
        "end_date": {"type": "string"},
    },
}

#: PRD P1「自动识别拜访纪要、客户需求和后续任务」：v2 在纪要基础上抽取分类洞察。
#: 模板改动一律新增版本（v1 保留供历史调用回放）。
MEETING_MINUTES_V2_PROMPT_VERSION = "v2"

MEETING_MINUTES_V2_SYSTEM = (
    "你是企业差旅助手。根据现场拜访记录起草会议纪要，只输出 JSON。"
    "summary 用 2-4 句客观描述，decisions 只写客户当场确认的结论（没有就留空数组），"
    "follow_up_tasks 每条都必须有 title；"
    "insights 逐条抽取记录中出现的信息，kind 只能取 "
    "SUMMARY / CUSTOMER_NEED / OPPORTUNITY_SIGNAL / DEAL_TERM / COMMITMENT / STAGE_CHANGE，"
    "text 用记录里的原话或紧贴原意的概括，绝不推断没写的内容；"
    "requires_confirmation 对除 SUMMARY 外的所有 kind 必须为 true，只有 SUMMARY 可为 false。"
    "不要编造未在输入中出现的事实，也不要把推断写成已确认结论。"
)

#: 服务端强制确认的类别（PRD 页面 07 §8.2 默认处理表）：模型说 false 也改回 true。
MUST_CONFIRM_KINDS = frozenset(
    {"CUSTOMER_NEED", "OPPORTUNITY_SIGNAL", "DEAL_TERM", "COMMITMENT", "STAGE_CHANGE"}
)
INSIGHT_KINDS = frozenset({"SUMMARY"}) | MUST_CONFIRM_KINDS

MEETING_MINUTES_V2_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["title", "summary", "decisions", "follow_up_tasks", "insights"],
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "decisions": {"type": "array"},
        "follow_up_tasks": {"type": "array"},
        "insights": {"type": "array"},
    },
}

ASSISTANT_TEMPLATE_ID = "assistant_reply"
ASSISTANT_PROMPT_VERSION = "v1"

ASSISTANT_SYSTEM = (
    "你是企业差旅助手（对话入口）。只根据输入的上下文事实回答：行程状态、待办数量、"
    "对话历史。不编造未提供的数据；涉及执行的动作只描述入口（行动中心/计划/纪要），"
    "不声称已经执行。reply 用 1-3 句中文，必要时给出下一步建议。"
)

ASSISTANT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["reply"],
    "properties": {"reply": {"type": "string"}},
}

def trip_creation_intent(message: str) -> bool:
    """判断消息是否在**请求创建**一次出差（而不是询问现有行程）。

    关键口径：必须出现"出差"，或"行程"叠加创建类动词，或英文 trip 叠加 create/plan 类词。
    仅问"这趟行程需要我做什么"不命中——那是在询问状态，不是创建请求。
    """
    text = message.lower()
    if "出差" in message:
        return True
    if "行程" in message and any(k in message for k in ("创建", "新建", "安排", "规划")):
        return True
    return "trip" in text and any(k in text for k in ("create", "plan a", "new trip", "book"))


PROMPT_TEMPLATES: dict[tuple[str, str], dict[str, Any]] = {
    (MEETING_MINUTES_TEMPLATE_ID, MEETING_MINUTES_PROMPT_VERSION): {
        "system": MEETING_MINUTES_SYSTEM,
        "schema": MEETING_MINUTES_SCHEMA,
        "task": "draft_meeting_minutes",
    },
    (TRIP_INTAKE_TEMPLATE_ID, TRIP_INTAKE_PROMPT_VERSION): {
        "system": TRIP_INTAKE_SYSTEM,
        "schema": TRIP_INTAKE_SCHEMA,
        "task": "trip_intake",
    },
    (MEETING_MINUTES_TEMPLATE_ID, MEETING_MINUTES_V2_PROMPT_VERSION): {
        "system": MEETING_MINUTES_V2_SYSTEM,
        "schema": MEETING_MINUTES_V2_SCHEMA,
        "task": "draft_meeting_minutes",
    },
    (ASSISTANT_TEMPLATE_ID, ASSISTANT_PROMPT_VERSION): {
        "system": ASSISTANT_SYSTEM,
        "schema": ASSISTANT_SCHEMA,
        "task": "assistant_reply",
    },
}


def get_template(template_id: str, version: str) -> dict[str, Any]:
    return PROMPT_TEMPLATES[(template_id, version)]


__all__ = [
    "ASSISTANT_PROMPT_VERSION",
    "INSIGHT_KINDS",
    "MUST_CONFIRM_KINDS",
    "MEETING_MINUTES_V2_PROMPT_VERSION",
    "MEETING_MINUTES_V2_SCHEMA",
    "MEETING_MINUTES_V2_SYSTEM",
    "TRIP_INTAKE_PROMPT_VERSION",
    "TRIP_INTAKE_SCHEMA",
    "TRIP_INTAKE_SYSTEM",
    "TRIP_INTAKE_TEMPLATE_ID",
    "ASSISTANT_SCHEMA",
    "ASSISTANT_SYSTEM",
    "ASSISTANT_TEMPLATE_ID",
    "MEETING_MINUTES_PROMPT_VERSION",
    "MEETING_MINUTES_SCHEMA",
    "MEETING_MINUTES_SYSTEM",
    "MEETING_MINUTES_TEMPLATE_ID",
    "PROMPT_TEMPLATES",
    "get_template",
    "trip_creation_intent",
]
