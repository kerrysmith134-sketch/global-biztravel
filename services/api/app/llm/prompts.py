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

PROMPT_TEMPLATES: dict[tuple[str, str], dict[str, Any]] = {
    (MEETING_MINUTES_TEMPLATE_ID, MEETING_MINUTES_PROMPT_VERSION): {
        "system": MEETING_MINUTES_SYSTEM,
        "schema": MEETING_MINUTES_SCHEMA,
        "task": "draft_meeting_minutes",
    },
}


def get_template(template_id: str, version: str) -> dict[str, Any]:
    return PROMPT_TEMPLATES[(template_id, version)]


__all__ = [
    "MEETING_MINUTES_PROMPT_VERSION",
    "MEETING_MINUTES_SCHEMA",
    "MEETING_MINUTES_SYSTEM",
    "MEETING_MINUTES_TEMPLATE_ID",
    "PROMPT_TEMPLATES",
    "get_template",
]
