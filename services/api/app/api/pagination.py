"""列表分页。

契约统一返回 ``{"items": [...], "next_cursor": null}``。这里用**偏移量游标**：
把下一页的 offset 编码成字符串。选择它而不是 keyset 分页，是因为出行/客户/地点这些列表
都是几十到几千行的组织级小集合，偏移量足够，而 keyset 会逼客户端理解排序键。
``next_cursor`` 为 ``null`` 就代表没有下一页——客户端只依赖这一个信号。
"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel

from app.api.errors import BadRequest

T = TypeVar("T")

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def clamp_limit(limit: int | None, *, default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT) -> int:
    if limit is None:
        return default
    if limit < 1:
        raise BadRequest("limit 必须大于 0", details={"limit": limit})
    return min(limit, maximum)


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        offset = int(cursor)
    except ValueError as exc:
        raise BadRequest("cursor 无效", details={"cursor": cursor}) from exc
    if offset < 0:
        raise BadRequest("cursor 无效", details={"cursor": cursor})
    return offset


class Page(BaseModel):
    """占位以固定导入路径；真实泛型分页模型见 :class:`app.api.schemas.Page`。"""


def next_cursor(items: list, offset: int, limit: int) -> str | None:
    """只在"取满了"时才给下一页指针，避免让客户端多打一次空请求。"""
    if len(items) < limit:
        return None
    return str(offset + limit)