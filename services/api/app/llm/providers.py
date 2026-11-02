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
            if "insights" in (request.schema or {}).get("properties", {}):
                output["insights"] = self._meeting_insights(variables)
        elif request.task == "trip_intake":
            output = self._trip_intake(str(variables.get("message") or ""))
        elif request.task == "assistant_reply":
            trip = str(variables.get("trip_name") or "未命名出行")
            counts = variables.get("action_counts") or {}
            open_items = int(counts.get("DECISION", 0)) + int(counts.get("APPROVAL", 0)) + int(
                counts.get("FAILED", 0)
            )
            question = str(variables.get("message") or "").strip()
            reply = (
                f"当前行程「{trip}」，有 {open_items} 项需要你处理的事项"
                "（待决定/待审批/失败）。"
            )
            if question:
                reply += f"关于「{question[:40]}」：请到行动中心或对应行程页查看权威状态，我不会在这里代替执行。"
            else:
                reply += "可以直接问我行程状态，或去行动中心处理待办。"
            output = {"reply": reply}
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

    #: 关键词 → 洞察类别（PRD 页面 07 §8.2）。只在记录里真的出现这些措辞时才归类。
    _INSIGHT_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
        ("DEAL_TERM", ("报价", "价格", "折扣", "单价", "数量", "采购量", "付款条件")),
        ("COMMITMENT", ("交期", "交付", "承诺", "保证", "签约", "合同")),
        ("OPPORTUNITY_SIGNAL", ("扩产", "新增", "计划", "试点", "二期", "招标")),
        ("CUSTOMER_NEED", ("需要", "希望", "要求", "痛点", "支持")),
        ("STAGE_CHANGE", ("商机阶段", "进入谈判", "推进到", "转正式")),
    )

    @classmethod
    def _meeting_insights(cls, variables: dict[str, Any]) -> list[dict[str, Any]]:
        """从现场记录里按关键词抽洞察；命不中就不生成（不推断没写的内容）。"""
        notes = str(variables.get("notes") or "")
        insights: list[dict[str, Any]] = []
        if notes.strip():
            insights.append(
                {"kind": "SUMMARY", "text": notes.strip()[:200], "requires_confirmation": False}
            )
        for kind, keywords in cls._INSIGHT_KEYWORDS:
            hit = next((kw for kw in keywords if kw in notes), None)
            if hit is None:
                continue
            # 摘要句里包含关键词时，抽该句作为该类洞察的原文依据。
            sentence = next(
                (part.strip() for part in notes.split("。") if hit in part and part.strip()),
                notes.strip(),
            )
            insights.append(
                {"kind": kind, "text": sentence[:200], "requires_confirmation": True}
            )
        return insights

    @staticmethod
    def _trip_intake(message: str) -> dict[str, Any]:
        """确定性解析：只提取消息里**真实出现**的国家、天数与日期，缺失就缺失。"""
        from app.llm.prompts import trip_creation_intent

        if not trip_creation_intent(message):
            return {"has_trip_request": False}

        countries: list[str] = []
        known = {
            "德国": "DE", "荷兰": "NL", "法国": "FR", "比利时": "BE", "新加坡": "SG",
            "泰国": "TH", "越南": "VN", "马来西亚": "MY", "日本": "JP", "美国": "US",
            "英国": "GB", "爱尔兰": "IE", "芬兰": "FI", "中国": "CN", "印度": "IN",
            "阿联酋": "AE", "澳大利亚": "AU", "加拿大": "CA", "意大利": "IT", "西班牙": "ES",
            "波兰": "PL", "土耳其": "TR", "韩国": "KR",
        }
        for name, code in known.items():
            if name in message and code not in countries:
                countries.append(code)
        # 顺带识别裸 ISO 码（如 "DE/SG"）
        import re

        for code in re.findall(r"\b([A-Z]{2})\b", message):
            if code in known.values() and code not in countries:
                countries.append(code)

        days = None
        m = re.search(r"(\d{1,3})\s*天", message)
        if m:
            days = int(m.group(1))
        else:
            m = re.search(r"(\d{1,2})\s*周", message)
            if m:
                days = int(m.group(1)) * 7

        import re as _re
        from datetime import date, timedelta

        dates = _re.findall(r"(\d{4})-(\d{2})-(\d{2})", message)
        start_date: str | None = None
        end_date: str | None = None
        if dates:
            start_date = f"{dates[0][0]}-{dates[0][1]}-{dates[0][2]}"
            if len(dates) > 1:
                end_date = f"{dates[1][0]}-{dates[1][1]}-{dates[1][2]}"
        if start_date is None and days is not None:
            # 用户只给了天数：起点默认从明天开始（在回复卡片里明示，可被用户改口）。
            start_date = (date.today() + timedelta(days=1)).isoformat()

        trip_name: str | None = None
        if countries or days:
            region = "、".join(countries) if countries else "未指定目的地"
            trip_name = f"{region}出差" + (f" {days} 天" if days else "")

        result: dict[str, Any] = {"has_trip_request": True}
        if trip_name:
            result["name"] = trip_name
        if countries:
            result["countries"] = countries
        if days is not None:
            result["days"] = days
        if start_date:
            result["start_date"] = start_date
        if end_date:
            result["end_date"] = end_date
        return result

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
