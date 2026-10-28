"""LLM Provider 实现：本地确定性 stub 与 OpenAI-compatible HTTP provider。

设计文档 v2.0 第 7.1 章要求统一 ``LLMProvider.complete`` 并支持 failover。这里的两点取舍：

- **本地默认 stub，不外呼**。开发/测试与离线验收不应该依赖网络或密钥；stub 的输出是确定性的
  结构化 JSON，因此"同输入同输出"可重放。
- **真实 provider 只用于显式配置**（``GBT_LLM_PROVIDER=openai_compatible`` + base_url + key）。
  凭证只从环境变量读（红线 RL-05），不落文件、不进日志。未配置时抛 ``LLMUnavailable``，
  由网关走模板降级，而不是伪造一段"模型输出"。
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from app.config import get_settings
from app.llm.contracts import LLMRequest, LLMUnavailable, ProviderResult

_JSON_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class LLMProvider(Protocol):
    name: str

    def complete(self, request: LLMRequest) -> ProviderResult: ...


def _word_count(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).split())


class StubProvider:
    """确定性本地 provider：不联网、无密钥、输出可重放。

    它不是"假装有模型"：``name = "stub"`` 会原样写进 ``llm_invocations.model``，审计里
    一眼能看出这条产出来自本地规则而不是真实模型。
    """

    name = "stub"

    def complete(self, request: LLMRequest) -> ProviderResult:
        variables = request.variables
        if request.task == "draft_meeting_minutes":
            output = self._meeting_minutes(variables)
        elif request.task == "explain_plan":
            output = {
                "summary": (
                    f"已按 {variables.get('solver', '确定性求解器')} 生成候选行程；"
                    f"可行性由独立验证器判定，共 {variables.get('stop_count', 0)} 个停靠。"
                )
            }
        else:
            output = {"text": json.dumps(variables, ensure_ascii=False, sort_keys=True)}
        return ProviderResult(
            output=output,
            tokens_in=_word_count(variables),
            tokens_out=_word_count(output),
        )

    @staticmethod
    def _meeting_minutes(variables: dict[str, Any]) -> dict[str, Any]:
        customer = str(variables.get("customer_name") or "客户")
        title = str(variables.get("visit_title") or "商务拜访")
        notes = str(variables.get("notes") or "").strip()
        outcome = str(variables.get("outcome") or "")
        tasks = variables.get("follow_up_tasks") or []
        follow_ups = [
            {
                "title": str(task.get("title") or "").strip(),
                "due_date": task.get("due_date"),
            }
            for task in tasks
            if str(task.get("title") or "").strip()
        ]
        if not follow_ups:
            follow_ups = [{"title": "整理会议纪要与跟进事项并发送给客户", "due_date": None}]

        summary = f"与 {customer} 就「{title}」进行了会面。"
        if notes:
            summary += f"现场记录：{notes}"
        if outcome:
            summary += f" 结果：{outcome}。"
        return {
            "title": f"{customer} · {title} 会议纪要草稿",
            "summary": summary,
            "decisions": [],
            "follow_up_tasks": follow_ups,
        }


class OpenAICompatibleProvider:
    """OpenAI 兼容的 ``/chat/completions`` 实现。

    只支持 JSON object 输出（网关会再做 schema 校验）。网络错误、非 2xx、内容不是 JSON
    一律转成 ``LLMUnavailable``，让网关统一重试/降级。
    """

    name = "openai_compatible"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        settings = get_settings()
        self.base_url = (base_url or settings.llm_base_url or "").rstrip("/")
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model
        self.timeout_seconds = timeout_seconds
        if not self.base_url or not self.api_key:
            raise LLMUnavailable("未配置 llm_base_url / llm_api_key")

    def complete(self, request: LLMRequest) -> ProviderResult:
        import httpx

        settings = get_settings()
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": request.system
                    or "你是企业差旅助手，只输出符合给定 JSON Schema 的 JSON，不要输出解释。",
                },
                {
                    "role": "user",
                    "content": json.dumps(request.variables, ensure_ascii=False, sort_keys=True),
                },
            ],
            "temperature": (
                request.temperature if request.temperature is not None else settings.llm_temperature
            ),
            "seed": request.seed if request.seed is not None else settings.llm_seed,
            "max_tokens": request.max_tokens or settings.llm_max_tokens,
        }
        if request.schema is not None:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = httpx.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            cleaned = _JSON_FENCE.sub("", content).strip()
            output = json.loads(cleaned)
            if not isinstance(output, dict):
                raise LLMUnavailable("模型输出不是 JSON object")
        except LLMUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 —— 统一转成"不可用"，由网关降级
            raise LLMUnavailable(f"{type(exc).__name__}: {exc}") from exc

        usage = body.get("usage") or {}
        return ProviderResult(
            output=output,
            tokens_in=int(usage.get("prompt_tokens") or 0),
            tokens_out=int(usage.get("completion_tokens") or 0),
        )


def build_provider() -> LLMProvider:
    settings = get_settings()
    if settings.llm_provider == "openai_compatible":
        return OpenAICompatibleProvider()
    return StubProvider()


__all__ = [
    "LLMProvider",
    "OpenAICompatibleProvider",
    "StubProvider",
    "build_provider",
]
