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

PROMPT_TEMPLATES: dict[tuple[str, str], dict[str, Any]] = {
    (MEETING_MINUTES_TEMPLATE_ID, MEETING_MINUTES_PROMPT_VERSION): {
        "system": MEETING_MINUTES_SYSTEM,
        "schema": MEETING_MINUTES_SCHEMA,
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
    "ASSISTANT_SCHEMA",
    "ASSISTANT_SYSTEM",
    "ASSISTANT_TEMPLATE_ID",
    "MEETING_MINUTES_PROMPT_VERSION",
    "MEETING_MINUTES_SCHEMA",
    "MEETING_MINUTES_SYSTEM",
    "MEETING_MINUTES_TEMPLATE_ID",
    "PROMPT_TEMPLATES",
    "get_template",
]
