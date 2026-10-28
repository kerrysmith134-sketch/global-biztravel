"""LLM 网关的请求/响应契约（供 provider 与 gateway 共用，避免循环导入）。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class LLMRequest:
    task: str
    prompt_template_id: str
    prompt_version: str
    variables: dict[str, Any]
    schema: dict[str, Any] | None = None
    fallback: dict[str, Any] | None = None
    system: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    seed: int | None = None


@dataclass
class ProviderResult:
    output: dict[str, Any]
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass
class LLMResponse:
    output: dict[str, Any]
    invocation_id: Any
    model: str
    degraded: bool = False
    cached: bool = False
    error: str | None = None
    latency_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


class LLMUnavailable(RuntimeError):
    """provider 不可用或输出不可解析。网关会重试一次后走模板降级。"""


__all__ = ["LLMRequest", "LLMResponse", "LLMUnavailable", "ProviderResult"]
