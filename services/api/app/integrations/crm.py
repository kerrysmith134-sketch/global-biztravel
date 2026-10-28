"""CRM 适配器边界（PRD 访后跟进）。

默认 ``file`` 适配器只把待同步载荷写到本地 ``var/crm``，状态标记 ``PREPARED``（已准备，
不代表外部 CRM 已写入）。真实供应商需要实现 ``CrmProvider.sync`` 并配置凭证；未配置时
``unavailable`` 显式失败，不伪造成功。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from app.config import get_settings


class CrmUnavailable(RuntimeError):
    pass


@dataclass
class CrmSyncResult:
    status: str
    external_id: str | None
    detail: str


class CrmProvider(Protocol):
    name: str

    def sync(
        self, organization_id: str, record_id: str, payload: dict[str, Any]
    ) -> CrmSyncResult: ...


class FileCrmProvider:
    name = "file"

    def sync(
        self, organization_id: str, record_id: str, payload: dict[str, Any]
    ) -> CrmSyncResult:
        root = Path(get_settings().crm_output_dir) / organization_id
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{record_id}.json"
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        return CrmSyncResult(
            status="PREPARED",
            external_id=f"file:{path}",
            detail="已写入本地待同步文件；未连接真实 CRM",
        )


class UnavailableCrmProvider:
    name = "unavailable"

    def sync(
        self, organization_id: str, record_id: str, payload: dict[str, Any]
    ) -> CrmSyncResult:  # noqa: ARG002
        raise CrmUnavailable("CRM 供应商未配置")


def get_crm_provider() -> CrmProvider:
    if get_settings().crm_provider == "unavailable":
        return UnavailableCrmProvider()
    return FileCrmProvider()


__all__ = [
    "CrmProvider",
    "CrmSyncResult",
    "CrmUnavailable",
    "FileCrmProvider",
    "UnavailableCrmProvider",
    "get_crm_provider",
]
