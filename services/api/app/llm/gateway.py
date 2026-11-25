"""LLM 网关：调用记录、schema 校验、重放缓存、重试与模板降级。

设计文档 v2.0 第 7 章。网关是**唯一**允许调用模型的地方，负责把"一次模型调用"变成一条
可审计、可重放、可降级的记录：

1. **先算 ``input_hash``**（task + template + version + model + variables 的规范 JSON）。
   相同输入命中缓存时不再计费，但仍然写一条 ``cached=true`` 的调用记录——"这次请求发生过"
   是审计事实。
2. **输出必须过 schema 校验**。校验失败重试一次；仍失败则使用调用方提供的模板兜底，
   ``degraded=true`` 且 ``error`` 写明原因。绝不让不合格的模型输出进入业务。
3. **不让 LLM 参与可行性判定**。本网关只服务解释/草拟类任务；规划结论仍来自求解器 + 验证器。
4. **凭证只来自环境变量**（provider 层负责），网关不读、不写、不记录任何密钥。

时间与成本：``latency_ms`` 用单调时钟测 provider 调用；``cost_estimate`` 按配置的
"每千 token 单价"估算，默认 0（本地 stub 不产生费用）。
"""

from __future__ import annotations

import json
import time
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.llm.contracts import LLMRequest, LLMResponse, LLMUnavailable, ProviderResult
from app.llm.providers import LLMProvider, build_provider
from app.models.llm import LLMInvocation

_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "number": (int, float),
    "integer": int,
    "boolean": bool,
}


class _UnavailableProvider:
    """配置了真实 provider 但初始化失败（缺 base_url/key）时的占位。

    它不伪造输出：``complete`` 一律抛错，网关据此走降级并在 ``error`` 里保留原因。
    """

    def __init__(self, message: str) -> None:
        self.name = "unavailable"
        self._message = message

    def complete(self, request: LLMRequest) -> ProviderResult:  # noqa: ARG002
        raise LLMUnavailable(self._message)


class LLMGateway:
    def __init__(self, provider: LLMProvider | None = None) -> None:
        self.provider = provider or build_provider()

    @property
    def model_name(self) -> str:
        return str(getattr(self.provider, "model", self.provider.name))

    def complete(
        self,
        session: Session,
        organization_id: UUID | str,
        request: LLMRequest,
        *,
        trace_id: str | None = None,
    ) -> LLMResponse:
        settings = get_settings()
        model = self.model_name
        input_hash = self._input_hash(request, model)

        cached = self._cache_lookup(session, organization_id, request, model, input_hash)
        if cached is not None:
            invocation = LLMInvocation(
                organization_id=str(organization_id),
                task=request.task,
                model=model,
                prompt_template_id=request.prompt_template_id,
                prompt_version=request.prompt_version,
                input_hash=input_hash,
                output_json=cached.output_json,
                tokens_in=0,
                tokens_out=0,
                latency_ms=0,
                cost_estimate=0,
                degraded=False,
                cached=True,
                trace_id=trace_id or _trace_id(),
            )
            session.add(invocation)
            session.flush()
            return LLMResponse(
                output=dict(cached.output_json or {}),
                invocation_id=invocation.id,
                model=model,
                cached=True,
            )

        error: str | None = None
        result: ProviderResult | None = None
        latency_ms = 0
        # 设计文档 §7.4：校验失败重试一次；两次都失败才降级。
        for attempt in range(2):
            started = time.perf_counter()
            try:
                candidate = self.provider.complete(request)
                latency_ms = int((time.perf_counter() - started) * 1000)
                problem = _validate(candidate.output, request.schema)
                if problem is not None:
                    raise LLMUnavailable(f"schema 校验失败：{problem}")
                result = candidate
                break
            except LLMUnavailable as exc:
                latency_ms = int((time.perf_counter() - started) * 1000)
                error = str(exc)
            except Exception as exc:  # noqa: BLE001 —— provider 抛什么都转成降级，不炸业务
                latency_ms = int((time.perf_counter() - started) * 1000)
                error = f"{type(exc).__name__}: {exc}"
            if attempt == 0:
                # 第一次失败后立刻重试；stub/网络瞬时错误都可能自愈。
                continue

        degraded = result is None
        if result is None:
            output = dict(request.fallback or {"text": ""})
        else:
            output = result.output

        tokens_in = result.tokens_in if result is not None else 0
        tokens_out = result.tokens_out if result is not None else 0
        cost = (tokens_in + tokens_out) / 1000 * settings.llm_cost_per_1k_tokens

        invocation = LLMInvocation(
            organization_id=str(organization_id),
            task=request.task,
            model=model,
            prompt_template_id=request.prompt_template_id,
            prompt_version=request.prompt_version,
            input_hash=input_hash,
            output_json=output,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_estimate=cost,
            degraded=degraded,
            cached=False,
            error=error if degraded else None,
            trace_id=trace_id or _trace_id(),
        )
        session.add(invocation)
        session.flush()
        return LLMResponse(
            output=output,
            invocation_id=invocation.id,
            model=model,
            degraded=degraded,
            error=error if degraded else None,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _input_hash(request: LLMRequest, model: str) -> str:
        material = json.dumps(
            {
                "task": request.task,
                "template": request.prompt_template_id,
                "version": request.prompt_version,
                "model": model,
                "variables": request.variables,
            },
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return "sha256:" + sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _cache_lookup(
        session: Session,
        organization_id: UUID | str,
        request: LLMRequest,
        model: str,
        input_hash: str,
    ) -> LLMInvocation | None:
        return session.scalars(
            select(LLMInvocation)
            .where(
                LLMInvocation.organization_id == str(organization_id),
                LLMInvocation.task == request.task,
                LLMInvocation.model == model,
                LLMInvocation.prompt_template_id == request.prompt_template_id,
                LLMInvocation.prompt_version == request.prompt_version,
                LLMInvocation.input_hash == input_hash,
                LLMInvocation.degraded.is_(False),
                LLMInvocation.cached.is_(False),
                LLMInvocation.output_json.is_not(None),
            )
            .order_by(LLMInvocation.created_at.desc())
            .limit(1)
        ).first()


def _validate(output: Any, schema: dict[str, Any] | None) -> str | None:
    """最小 JSON Schema 校验：顶层类型 + required + properties 类型。

    刻意不引入完整 jsonschema 依赖：本系统用到的 schema 都是网关自己声明的固定结构，
    校验目标只是"模型没把字段名写错/没返回半截 JSON"，不是支持任意 schema。
    """
    if schema is None:
        return None
    expected = schema.get("type", "object")
    allowed = _TYPE_MAP.get(expected)
    if allowed is not None and not isinstance(output, allowed):
        return f"顶层类型应为 {expected}，实际 {type(output).__name__}"
    if expected != "object":
        return None
    if not isinstance(output, dict):
        return "顶层应为 object"
    for key in schema.get("required", []):
        if key not in output:
            return f"缺少必填字段 {key}"
    for key, spec in (schema.get("properties") or {}).items():
        if key not in output:
            continue
        spec_type = spec.get("type")
        spec_allowed = _TYPE_MAP.get(spec_type)
        if spec_allowed is not None and output[key] is not None and not isinstance(
            output[key], spec_allowed
        ):
            return f"字段 {key} 应为 {spec_type}"
        item_spec = spec.get("items")
        if spec_type == "array" and isinstance(output[key], list) and isinstance(item_spec, dict):
            item_type = item_spec.get("type")
            item_allowed = _TYPE_MAP.get(item_type)
            if item_type is not None and item_allowed is not None:
                for index, item in enumerate(output[key]):
                    if not isinstance(item, item_allowed):
                        return f"字段 {key}[{index}] 应为 {item_type}"
    return None


def _trace_id() -> str | None:
    try:
        from app.api.trace import get_trace_id

        return get_trace_id()
    except Exception:  # noqa: BLE001 —— 网关也可能在 Worker/测试里被调用，没有请求上下文
        return None


_gateway: LLMGateway | None = None


def get_gateway() -> LLMGateway:
    """进程级单例。provider 配置错误不会在 import 期炸掉服务，而是走降级路径。"""
    global _gateway
    if _gateway is None:
        try:
            _gateway = LLMGateway()
        except LLMUnavailable as exc:
            _gateway = LLMGateway(provider=_UnavailableProvider(str(exc)))
    return _gateway


__all__ = ["LLMGateway", "get_gateway"]
