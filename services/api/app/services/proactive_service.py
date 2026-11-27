"""主动式运行时服务：扫描触发 → 生成建议 → 抑制/节流 → 人审 → 受控执行 → 审计。

设计文档 v2.0 第 8 章的闭环在这里落地。几个必须提前说清的设计取舍：

**1. 规则是确定性的，LLM 不参与。**
本模块只做"根据可核对的事实生成提醒"：出发时间、国家列表、坐标缺失、计划未排项。
``rationale`` 是模板化文本，``evidence`` 是结构化引用。这与不变量 I1 一致——主动建议不能
凭模型判断宣布事实，只能把可验证的观察摆到人面前。将来接入 LLM 时，LLM 只负责润色措辞，
且必须带 ``llm_invocation_id``，不得改变证据。

**2. 抑制分三层，且都可解释。**
- 去重：``(trip, suggestion_type, dedupe_key)`` 唯一，同一件事只有一条建议（AT-P02）。
- 冷却：同类型建议在冷却窗口内再次触发 ⇒ 落库但 ``SUPPRESSED``，不打扰。
- 节流：低置信度、超出每日打扰上限 ⇒ 落库但 ``push_status=WITHHELD``，不推送。
把"抑制"和"未推送"分开记录，是为了让"用户为什么没看到"能回答。

**3. 执行只做"准备"。**
``approve`` 之后执行器生成的只是草稿/清单/待办，全部落 ``execution_result``，不对外发送、
不预订、不提交签证。这与第 8.5 章权限边界一致；对外动作必须由业务服务另行执行。

**4. 事务边界。**
本模块不 ``commit``，只 ``flush``。调用方（HTTP 请求 / Worker / 测试）决定提交点，
因此"建议落库"与"触发记录落库"在同一次提交里，不会出现半条闭环。
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.api.errors import Conflict, NotFound, Unprocessable
from app.config import get_settings
from app.models.geo import Place
from app.models.planning import PlanVersion
from app.models.proactive import (
    Approval,
    ProactiveSuggestion,
    ProactiveTrigger,
    SuggestionAudit,
)
from app.models.trip import Trip, Visit
from app.services import (
    approval_policy_service,
    evidence_service,
    external_impact_service,
    external_service,
    outbox_service,
    visa_service,
)

#: 建议类型。集中登记，避免规则、执行器与测试各写一套字符串。
SUGGESTION_VISA_REMINDER = "VISA_REQUIREMENT_REMINDER"
SUGGESTION_PLACE_COORDINATES = "PLACE_COORDINATES_MISSING"
SUGGESTION_PLAN_UNPLACED = "PLAN_UNPLACED_VISITS"
SUGGESTION_VISA_POLICY = "VISA_POLICY_UPDATE"
SUGGESTION_WEATHER = "WEATHER_DISRUPTION_ALERT"
SUGGESTION_FX = "FX_RATE_ALERT"
SUGGESTION_FLIGHT = "FLIGHT_STATUS_ALERT"

#: 规则 id 与触发源类型。设计文档 §8.1 的四分类在这里全部有落点：
#: SCHEDULED / STATE / EVENT / EXTERNAL。
RULE_VISA_CHECK = "visa_check"
RULE_MISSING_COORDINATES = "missing_coordinates"
RULE_UNPLACED_VISITS = "unplaced_visits"
RULE_EXTERNAL_SIGNAL = "external_signal"

TRIGGER_SCHEDULED = "SCHEDULED"
TRIGGER_STATE = "STATE"
TRIGGER_EVENT = "EVENT"
TRIGGER_EXTERNAL = "EXTERNAL"

#: 外部信号类型 → (建议类型, 中文标签)。未知类型不生成建议：宁可漏，不可编。
_EXTERNAL_SUGGESTIONS: dict[str, tuple[str, str]] = {
    "VISA_POLICY_CHANGE": (SUGGESTION_VISA_POLICY, "签证政策变化"),
    "WEATHER_DISRUPTION": (SUGGESTION_WEATHER, "天气/中断预警"),
    "FX_VOLATILITY": (SUGGESTION_FX, "汇率波动预警"),
    "FLIGHT_STATUS": (SUGGESTION_FLIGHT, "航班状态变化"),
}

_FLIGHT_OPERATIONAL_LABELS = {
    "SCHEDULE_CHANGE": "航班时刻调整",
    "DEPARTURE_DELAY": "出发延误",
    "ARRIVAL_DELAY": "到达延误",
    "CANCELLED": "航班取消",
    "DIVERTED": "航班备降",
    "GATE_CHANGE": "登机口变更",
}
_FLIGHT_OPERATIONAL_INTEGRITY_FAILURES = frozenset({
    "OPERATIONAL_UPDATE_REFERENCE_INVALID",
    "OPERATIONAL_UPDATE_SCOPE_MISMATCH",
    "OPERATIONAL_UPDATE_EVIDENCE_MISMATCH",
    "OPERATIONAL_UPDATE_ENVELOPE_MISMATCH",
})

#: 审批对象类型。审批表是通用的，这里只登记当前唯一用法。
APPROVAL_SUBJECT_SUGGESTION = "proactive_suggestion"

#: 不建议继续处理的状态：出行已收尾，再提醒没有意义。
_CLOSED_TRIP_STATUSES = ("COMPLETED", "CANCELLED")

_CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "visa_catalog.json"


@dataclass
class ScanReport:
    """一次扫描的结果，供 Worker 日志与集成测试断言。

    计数的含义要分清：``triggers`` 统计**新登记**的触发（去重命中不计），
    ``suggestions`` 统计**新落库**的建议；``pushed`` / ``withheld`` / ``suppressed``
    分别解释每条新建议为什么（没）打扰用户。
    """

    trips_scanned: int = 0
    triggers: int = 0
    suggestions: int = 0
    pushed: int = 0
    withheld: int = 0
    suppressed: int = 0
    deduped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def did_work(self) -> bool:
        return bool(self.triggers or self.suggestions)


@dataclass(frozen=True)
class Candidate:
    """一条规则命中的结果：尚未落库，只描述"值得提醒什么"。"""

    trigger_type: str
    rule_id: str
    suggestion_type: str
    dedupe_key: str
    title: str
    rationale: str
    evidence: list[dict[str, Any]]
    proposed_action: dict[str, Any]
    confidence: float
    severity: str
    requires_approval: bool = True
    trigger_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class ApprovalOutcome:
    suggestion: ProactiveSuggestion
    approval: Approval
    executed: bool = True
    approval_count: int = 1
    required_approvals: int = 1


@dataclass
class DismissOutcome:
    suggestion: ProactiveSuggestion
    weight_delta: float


# ---------------------------------------------------------------------------
# 规则
# ---------------------------------------------------------------------------
def _visa_candidate(
    session: Session, organization_id: UUID, trip: Trip, now: datetime
) -> Candidate | None:
    """出发前 T-72h 的签证/证件核验提醒（SCHEDULED）。

    只在"尚未出发 + 有目的国 + 进入提前量窗口"时触发。措辞刻意停在"需核验"，
    因为本地目录不是官方来源；把"需要签证"当成结论说出来就是伪造事实。
    """
    settings = get_settings()
    countries = [country.upper() for country in (trip.countries or []) if country]
    if not countries:
        return None

    departure = trip.window_start
    remaining = departure - now
    if remaining <= timedelta(0):
        return None
    if remaining > timedelta(hours=settings.proactive_visa_lookahead_hours):
        return None

    catalog = _visa_catalog()
    known = [code for code in countries if code in catalog["countries"]]
    unknown = [code for code in countries if code not in catalog["countries"]]
    hours_left = max(int(remaining.total_seconds() // 3600), 1)

    evidence: list[dict[str, Any]] = [
        {
            "kind": "TRIP_WINDOW",
            "ref": f"trip:{trip.id}@v{trip.version}",
            "retrieved_at": None,
        },
        {
            "kind": "VISA_RULE_CATALOG",
            "ref": f"{catalog['version']} (offline; manual confirmation required)",
            "retrieved_at": None,
        },
    ]
    for code in known:
        evidence.append(
            {
                "kind": "VISA_RULE",
                "ref": f"{code}: {catalog['countries'][code]['business_entry']}",
                "retrieved_at": None,
            }
        )
    for code in unknown:
        evidence.append(
            {
                "kind": "VISA_RULE_MISSING",
                "ref": f"{code}: 本地目录未收录，必须人工核验",
                "retrieved_at": None,
            }
        )

    known_labels = "、".join(f"{code}（{catalog['countries'][code]['label']}）" for code in known)
    if unknown:
        known_labels = "、".join(filter(None, [known_labels, "、".join(unknown) + "（目录未收录）"]))

    rationale = (
        f"出行将于约 {hours_left} 小时后开始，目的国：{known_labels}。"
        "本地规则目录只用于提醒，需按官方渠道人工核验签证类型、受理周期与材料要求。"
    )
    if unknown:
        rationale += " 其中部分国家未被目录收录，必须直接查官方信息。"

    confidence = 0.85 if not unknown else 0.70
    return Candidate(
        trigger_type=TRIGGER_SCHEDULED,
        rule_id=RULE_VISA_CHECK,
        suggestion_type=SUGGESTION_VISA_REMINDER,
        dedupe_key=f"visa_check:{departure.date().isoformat()}",
        title="出发前签证与证件核验",
        rationale=rationale,
        evidence=evidence,
        proposed_action={
            "kind": "PREPARE_CHECKLIST",
            "tool": "visa.prepare_checklist",
            "params": {
                "trip_id": str(trip.id),
                "countries": countries,
                "catalog_version": catalog["version"],
            },
        },
        confidence=confidence,
        severity="HIGH" if hours_left <= 48 else "MEDIUM",
        trigger_payload={
            "hours_until_departure": hours_left,
            "countries": countries,
            "catalog_version": catalog["version"],
        },
    )


def _missing_coordinates_candidate(
    session: Session, organization_id: UUID, trip: Trip, now: datetime
) -> Candidate | None:
    """拜访地点缺坐标（STATE）。缺坐标的地点无法参与优化，必须显式提醒而不是静默丢弃。"""
    rows = session.execute(
        select(Visit.id, Visit.place_id, Place.name, Place.city, Place.country_code)
        .join(Place, Place.id == Visit.place_id)
        .where(
            Visit.organization_id == organization_id,
            Visit.trip_id == trip.id,
            Visit.status.in_(("PLANNED", "SCHEDULED")),
            (Place.lat.is_(None)) | (Place.lon.is_(None)),
        )
    ).all()
    if not rows:
        return None

    place_ids = sorted({str(row.place_id) for row in rows})
    digest = sha256(":".join(place_ids).encode("utf-8")).hexdigest()[:16]
    evidence = [
        {
            "kind": "PLACE_MISSING_COORDINATES",
            "ref": f"{row.place_id}: {row.name or row.city or '未命名地点'}",
            "retrieved_at": None,
        }
        for row in rows
    ]
    return Candidate(
        trigger_type=TRIGGER_STATE,
        rule_id=RULE_MISSING_COORDINATES,
        suggestion_type=SUGGESTION_PLACE_COORDINATES,
        dedupe_key=f"missing_coordinates:{digest}",
        title=f"{len(place_ids)} 个拜访地点缺少坐标",
        rationale=(
            "这些地点没有经纬度，行程优化会把它们排除或降级为城市中心估算。"
            "补全坐标后重新生成计划，才能保证路线与时间窗判断可靠。"
        ),
        evidence=evidence,
        proposed_action={
            "kind": "PREPARE_GEOCODING",
            "tool": "place.prepare_geocoding",
            "params": {"trip_id": str(trip.id), "place_ids": place_ids},
        },
        confidence=0.95,
        severity="MEDIUM",
        trigger_payload={"place_ids": place_ids, "visit_ids": [str(row.id) for row in rows]},
    )


def _unplaced_candidate(
    session: Session, organization_id: UUID, trip: Trip, now: datetime
) -> Candidate | None:
    """生效计划里仍有未排项（STATE）。这是"计划看起来成功、实际没排进去"的显式补救提醒。"""
    plan = session.scalars(
        select(PlanVersion).where(
            PlanVersion.organization_id == organization_id,
            PlanVersion.trip_id == trip.id,
            PlanVersion.status == "ACTIVE",
        )
    ).one_or_none()
    if plan is None:
        return None
    unplaced = plan.unplaced or []
    if not unplaced:
        return None

    visit_ids = sorted({str(item.get("visit_id")) for item in unplaced if item.get("visit_id")})
    digest = sha256(":".join(visit_ids).encode("utf-8")).hexdigest()[:16]
    evidence = [
        {
            "kind": "PLAN_UNPLACED",
            "ref": f"{item.get('visit_id')}: {item.get('reason') or 'UNKNOWN'}",
            "retrieved_at": None,
        }
        for item in unplaced
    ]
    evidence.append(
        {
            "kind": "PLAN_VERSION",
            "ref": f"{plan.id} (v{plan.version_number})",
            "retrieved_at": None,
        }
    )
    return Candidate(
        trigger_type=TRIGGER_STATE,
        rule_id=RULE_UNPLACED_VISITS,
        suggestion_type=SUGGESTION_PLAN_UNPLACED,
        dedupe_key=f"unplaced:{plan.id}:{digest}",
        title=f"生效计划中有 {len(unplaced)} 个拜访未排入",
        rationale=(
            "独立验证器已把这些拜访标记为不可行（时间窗、日上限或交通约束）。"
            "请确认是否调整时间窗、拆分出行日，或明确放弃这些拜访。"
        ),
        evidence=evidence,
        proposed_action={
            "kind": "PREPARE_PLAN_ADJUSTMENT",
            "tool": "plan.prepare_adjustment",
            "params": {"plan_version_id": str(plan.id), "visit_ids": visit_ids},
        },
        confidence=0.90,
        severity="HIGH",
        trigger_payload={"plan_version_id": str(plan.id), "visit_ids": visit_ids},
    )


def _external_candidates(
    session: Session, organization_id: UUID, trip: Trip, now: datetime
) -> list[Candidate]:
    """外部信号（EXTERNAL）：签证政策/天气/汇率/航班变化。

    只消费已落库、未过期的信号；没有信号就什么都不产生。措辞始终是"外部源报告了…，
    需人工复核"，不把供应商结论直接当成事实宣布。
    """
    signals = external_service.active_for_trip(session, organization_id, trip, now=now)
    candidates: list[Candidate] = []
    for signal in signals:
        mapping = _EXTERNAL_SUGGESTIONS.get(signal.signal_type)
        if mapping is None:
            continue
        suggestion_type, label = mapping
        payload = signal.payload or {}
        title = str(payload.get("title") or f"{label}：{signal.subject_ref}")
        summary = str(
            payload.get("summary")
            or f"外部源 {signal.provider} 报告了一条与本次出行相关的变化，需人工复核。"
        )
        severity = payload.get("severity")
        if severity not in ("LOW", "MEDIUM", "HIGH"):
            severity = "HIGH"
        evidence = [
            {
                "kind": "EXTERNAL_SIGNAL",
                "ref": f"{signal.provider}:{signal.id} ({signal.signal_type})",
                "retrieved_at": signal.observed_at.isoformat(),
            }
        ]
        impact_assessment = None
        impact_note = ""
        if signal.signal_type == "WEATHER_DISRUPTION":
            impact_assessment = external_impact_service.assess_weather_signal_impact(
                session, organization_id, trip, signal
            )
            impact_note = f"行程影响评估：{impact_assessment['explanation']}"
            if impact_assessment.get("plan_version_id"):
                evidence.append(
                    {
                        "kind": "ACTIVE_PLAN_VERSION",
                        "ref": (
                            f"plan-version:{impact_assessment['plan_version_id']}"
                            f"@v{impact_assessment['plan_version_number']}"
                        ),
                        "retrieved_at": None,
                    }
                )
            for stop in impact_assessment.get("matched_stops", []):
                evidence.append(
                    {
                        "kind": "PLAN_STOP",
                        "ref": (
                            f"plan-stop:{stop['plan_stop_id']};visit:{stop['visit_id']};"
                            f"time_overlap:{str(stop['time_window_overlap']).lower()}"
                        ),
                        "retrieved_at": None,
                    }
                )
            for leg in impact_assessment.get("related_travel_legs", []):
                evidence.append(
                    {
                        "kind": "PLAN_TRAVEL_LEG",
                        "ref": (
                            f"plan-travel-leg:{leg['plan_travel_leg_id']};"
                            f"mode:{leg['mode']};relation:{leg['relation']};"
                            f"estimated_slack_minutes:{leg['estimated_schedule_slack_minutes']};"
                            f"stale:{str(leg['stale']).lower()}"
                        ),
                        "retrieved_at": None,
                    }
                )
        elif signal.signal_type == "FLIGHT_STATUS":
            impact_assessment = external_impact_service.assess_flight_signal_impact(
                session, organization_id, trip, signal
            )
            impact_note = f"航班关联评估：{impact_assessment['explanation']}"
            if impact_assessment.get("plan_version_id"):
                evidence.append(
                    {
                        "kind": "ACTIVE_PLAN_VERSION",
                        "ref": (
                            f"plan-version:{impact_assessment['plan_version_id']}"
                            f"@v{impact_assessment['plan_version_number']}"
                        ),
                        "retrieved_at": None,
                    }
                )
            if impact_assessment.get("flight_segment_id"):
                evidence.append(
                    {
                        "kind": "FLIGHT_SEGMENT",
                        "ref": (
                            f"flight-segment:{impact_assessment['flight_segment_id']};"
                            f"designator:{impact_assessment.get('flight_designator') or 'unknown'};"
                            f"route:{impact_assessment.get('departure_airport_iata') or '???'}-"
                            f"{impact_assessment.get('arrival_airport_iata') or '???'}"
                        ),
                        "retrieved_at": None,
                    }
                )
            if impact_assessment.get("binding_id"):
                evidence.append(
                    {
                        "kind": "FLIGHT_SEGMENT_PLAN_BINDING",
                        "ref": (
                            f"flight-binding:{impact_assessment['binding_id']};"
                            f"plan-version:{impact_assessment.get('plan_version_id') or 'unknown'}"
                        ),
                        "retrieved_at": None,
                    }
                )
            linked_leg = impact_assessment.get("linked_air_leg")
            if linked_leg:
                evidence.append(
                    {
                        "kind": "PLAN_TRAVEL_LEG",
                        "ref": (
                            f"plan-travel-leg:{linked_leg['plan_travel_leg_id']};"
                            f"mode:{linked_leg['mode']};"
                            f"travel_minutes:{linked_leg['travel_minutes']};"
                            f"stale:{str(linked_leg['stale']).lower()}"
                        ),
                        "retrieved_at": None,
                    }
                )
            if impact_assessment.get("operational_update_id"):
                evidence.append(
                    {
                        "kind": "FLIGHT_OPERATIONAL_UPDATE",
                        "ref": (
                            f"flight-operations:{impact_assessment['operational_update_id']};"
                            f"provider:{impact_assessment.get('provider')};"
                            f"event:{impact_assessment.get('provider_event_id')};"
                            f"type:{impact_assessment.get('update_type')};"
                            f"sha256:{impact_assessment.get('content_sha256')}"
                        ),
                        "retrieved_at": impact_assessment.get("observed_at"),
                    }
                )
            for stop in impact_assessment.get("stop_impacts", []):
                evidence.append(
                    {
                        "kind": "PLAN_STOP_IMPACT",
                        "ref": (
                            f"plan-stop:{stop['plan_stop_id']};visit:{stop['visit_id']};"
                            f"delay_minutes:{stop.get('delay_minutes')};"
                            f"appointment_status:{stop.get('appointment_status')};"
                            f"projected_arrival:{stop.get('projected_arrival_at') or 'unknown'}"
                        ),
                        "retrieved_at": None,
                    }
                )
            for leg in impact_assessment.get("related_travel_legs", []):
                evidence.append(
                    {
                        "kind": "PLAN_TRAVEL_LEG_BUFFER",
                        "ref": (
                            f"from:{leg['from_stop_id']};to:{leg['to_stop_id']};"
                            f"travel_minutes:{leg['travel_minutes']};"
                            f"schedule_buffer_minutes:{leg['schedule_buffer_minutes']};"
                            f"delay_after_buffer_minutes:{leg['delay_after_buffer_minutes']}"
                        ),
                        "retrieved_at": None,
                    }
                )
            for leg in impact_assessment.get("unlinked_air_legs", []):
                evidence.append(
                    {
                        "kind": "UNLINKED_AIR_TRAVEL_LEG",
                        "ref": (
                            f"plan-travel-leg:{leg['plan_travel_leg_id']};"
                            f"callsign:{impact_assessment.get('callsign') or 'unknown'};"
                            "explicit_link_required:true"
                        ),
                        "retrieved_at": None,
                    }
                )
        # A normalized operational event is authoritative for display severity.
        # Provider-supplied title/severity are advisory text and must never downgrade
        # a genuinely missed customer appointment or inflate an absorbed delay.
        canonical_signal_payload = payload
        if signal.signal_type == "FLIGHT_STATUS" and payload.get("flight_operational_update"):
            impact_status = impact_assessment.get("status") if impact_assessment else None
            if impact_status in _FLIGHT_OPERATIONAL_INTEGRITY_FAILURES:
                # Fail closed: do not create an actionable suggestion from an event
                # whose server-owned reference, digest, or envelope failed verification.
                continue
            if impact_assessment and impact_assessment.get("operational_update_id"):
                update_type = str(impact_assessment.get("update_type") or "")
                designator = str(impact_assessment.get("flight_designator") or "航班")
                title = f"{designator} {_FLIGHT_OPERATIONAL_LABELS.get(update_type, '运营状态变化')}"
                missed_visits = impact_assessment.get("missed_visit_ids", [])
                affected_visits = impact_assessment.get("affected_visit_ids", [])
                if update_type in ("CANCELLED", "DIVERTED") or missed_visits:
                    severity = "HIGH"
                elif update_type == "GATE_CHANGE" or impact_status == "NO_OPERATIONAL_DELAY":
                    severity = "LOW"
                elif (
                    impact_status == "OPERATIONAL_DELAY_ASSESSED"
                    and not affected_visits
                ):
                    severity = "LOW"
                else:
                    severity = "MEDIUM"
                canonical_signal_payload = {
                    "operational_update_id": impact_assessment["operational_update_id"],
                    "content_sha256": impact_assessment.get("content_sha256"),
                    "provider_event_id": impact_assessment.get("provider_event_id"),
                    "update_type": update_type,
                }

        # Weather is re-evaluated when the active plan changes; flight signals are
        # re-evaluated when the active plan or its explicit booking binding changes.
        # Signals without a deterministic impact assessment remain payload-deduped.
        digest_input = canonical_signal_payload
        if impact_assessment is not None:
            digest_input = {
                "signal_payload": canonical_signal_payload,
                "plan_impact": {
                    "status": impact_assessment.get("status"),
                    "plan_version_id": impact_assessment.get("plan_version_id"),
                    "affected_visit_ids": impact_assessment.get("affected_visit_ids", []),
                    "related_travel_leg_ids": [
                        leg_id
                        for leg in impact_assessment.get("related_travel_legs", [])
                        for leg_id in (
                            leg.get("plan_travel_leg_ids")
                            or ([leg["plan_travel_leg_id"]] if leg.get("plan_travel_leg_id") else [])
                        )
                    ],
                    "unlinked_air_leg_ids": [
                        leg["plan_travel_leg_id"]
                        for leg in impact_assessment.get("unlinked_air_legs", [])
                    ],
                    "flight_segment_id": impact_assessment.get("flight_segment_id"),
                    "flight_designator": impact_assessment.get("flight_designator"),
                    "flight_binding_id": impact_assessment.get("binding_id"),
                    "linked_air_leg_ids": impact_assessment.get("linked_air_leg_ids", []),
                    "operational_update_id": impact_assessment.get("operational_update_id"),
                    "stop_impacts": [
                        {
                            "visit_id": item.get("visit_id"),
                            "delay_minutes": item.get("delay_minutes"),
                            "appointment_status": item.get("appointment_status"),
                            "projected_arrival_at": item.get("projected_arrival_at"),
                        }
                        for item in impact_assessment.get("stop_impacts", [])
                    ],
                    "missed_visit_ids": impact_assessment.get("missed_visit_ids", []),
                    "related_travel_legs": impact_assessment.get("related_travel_legs", []),
                },
            }
        digest = sha256(
            json.dumps(
                digest_input, sort_keys=True, ensure_ascii=False, default=str
            ).encode("utf-8")
        ).hexdigest()[:10]
        candidates.append(
            Candidate(
                trigger_type=TRIGGER_EXTERNAL,
                rule_id=RULE_EXTERNAL_SIGNAL,
                suggestion_type=suggestion_type,
                # payload 变化会得到新的 dedupe_key；同类型建议仍受 24h 冷却约束，不会连续打扰。
                dedupe_key=f"external:{signal.id}:{digest}",
                title=title,
                rationale=(
                    f"{summary} 来源：{signal.provider}，观察于 "
                    f"{signal.observed_at.isoformat()}；外部信息必须按官方渠道复核。"
                    f"{impact_note}"
                ),
                evidence=evidence,
                proposed_action={
                    "kind": "PREPARE_REVIEW",
                    "tool": "trip.prepare_external_review",
                    "params": {
                        "signal_id": str(signal.id),
                        "signal_type": signal.signal_type,
                        "subject_ref": signal.subject_ref,
                        "impact_assessment": impact_assessment,
                    },
                },
                confidence=float(signal.confidence),
                severity=severity,
                trigger_payload={
                    "signal_id": str(signal.id),
                    "provider": signal.provider,
                    "signal_type": signal.signal_type,
                    "subject_ref": signal.subject_ref,
                    "impact_assessment": impact_assessment,
                },
            )
        )
    return candidates


#: 规则清单。顺序决定同一个出行上建议的生成顺序（只影响展示排序，不影响正确性）。
#: 规则可以返回单条或一小批（外部信号一次可能带来多条不同变化）。
Rule = Callable[[Session, UUID, Trip, datetime], Candidate | list[Candidate] | None]
RULES: tuple[Rule, ...] = (
    _visa_candidate,
    _missing_coordinates_candidate,
    _unplaced_candidate,
    _external_candidates,
)


# ---------------------------------------------------------------------------
# 扫描
# ---------------------------------------------------------------------------
def scan_organization(
    session: Session,
    organization_id: UUID | str,
    *,
    now: datetime | None = None,
    trip_ids: list[UUID] | None = None,
    trigger_type: str | None = None,
) -> ScanReport:
    """扫描一个组织的在途出行，把命中的规则变成建议。

    ``now`` 可注入（测试与回放），默认取数据库 ``clock_timestamp()``：主动式的"今天"
    必须与数据库里的租约/幂等记录同源，不能各取各的本地时钟。

    ``trigger_type`` 只在该次扫描由某个事件驱动时传入（例如计划激活）：它把触发源记成
    ``EVENT``，而不是伪装成定时扫描。规则本身不变——事件只是"现在值得重新看一遍"的信号。
    """
    org = UUID(str(organization_id))
    current = now or session.scalar(select(func.clock_timestamp()))
    report = ScanReport()

    stmt = select(Trip).where(
        Trip.organization_id == org,
        Trip.status.notin_(_CLOSED_TRIP_STATUSES),
    )
    if trip_ids:
        stmt = stmt.where(Trip.id.in_(trip_ids))
    trips = list(session.scalars(stmt.order_by(Trip.window_start)).all())
    report.trips_scanned = len(trips)

    for trip in trips:
        for rule in RULES:
            try:
                with session.begin_nested():
                    produced = rule(session, org, trip, current)
                    if produced is None:
                        continue
                    candidates = produced if isinstance(produced, list) else [produced]
                    for candidate in candidates:
                        if trigger_type is not None:
                            candidate = dataclasses.replace(candidate, trigger_type=trigger_type)
                        _record_candidate(session, org, trip, candidate, current, report)
            except Exception as exc:  # noqa: BLE001 —— 单条规则失败不能拖垮整轮扫描
                report.errors.append(f"{trip.id}/{rule.__name__}: {type(exc).__name__}: {exc}")

    return report


def scan_trip(
    session: Session,
    organization_id: UUID | str,
    trip_id: UUID | str,
    *,
    now: datetime | None = None,
    trigger_type: str | None = None,
) -> ScanReport:
    """只扫描一个出行。EVENT 触发（例如计划激活）用它，避免整租户重扫。"""
    return scan_organization(
        session,
        organization_id,
        now=now,
        trip_ids=[UUID(str(trip_id))],
        trigger_type=trigger_type,
    )


def _record_candidate(
    session: Session,
    organization_id: UUID,
    trip: Trip,
    candidate: Candidate,
    now: datetime,
    report: ScanReport,
) -> None:
    trigger = _claim_trigger(session, organization_id, trip, candidate, report)
    if trigger is None:
        return

    # 同一去重键已经有过建议：不再重复生成，也不重复提醒（AT-P02 的硬约束）。
    existing = session.scalars(
        select(ProactiveSuggestion).where(
            ProactiveSuggestion.organization_id == organization_id,
            ProactiveSuggestion.trip_id == trip.id,
            ProactiveSuggestion.suggestion_type == candidate.suggestion_type,
            ProactiveSuggestion.dedupe_key == candidate.dedupe_key,
        )
    ).one_or_none()
    if existing is not None:
        trigger.status = "DEDUPED"
        trigger.suggestion_id = existing.id
        trigger.detail = {"reason": "SUGGESTION_DEDUPE_HIT", "suggestion_id": str(existing.id)}
        report.deduped += 1
        return

    settings = get_settings()
    cooldown_start = now - timedelta(hours=settings.suggestion_cooldown_hours)
    in_cooldown = bool(
        session.scalar(
            select(func.count())
            .select_from(ProactiveSuggestion)
            .where(
                ProactiveSuggestion.organization_id == organization_id,
                ProactiveSuggestion.trip_id == trip.id,
                ProactiveSuggestion.suggestion_type == candidate.suggestion_type,
                ProactiveSuggestion.created_at >= cooldown_start,
            )
        )
    )

    penalty = _feedback_penalty(session, organization_id, candidate.suggestion_type, now)
    confidence = round(max(0.05, min(1.0, candidate.confidence - penalty)), 3)

    push_status = "PUSHED"
    push_reason: str | None = None
    status = "PENDING"
    if in_cooldown:
        # 冷却期内再次触发：仍然留痕，但不打扰。
        status = "SUPPRESSED"
        push_status = "WITHHELD"
        push_reason = "COOLDOWN"
    elif confidence < settings.suggestion_confidence_threshold:
        push_status = "WITHHELD"
        push_reason = "LOW_CONFIDENCE"
    elif _daily_cap_reached(session, organization_id, trip.owner_user_id, now):
        push_status = "WITHHELD"
        push_reason = "DAILY_CAP"

    suggestion = ProactiveSuggestion(
        organization_id=organization_id,
        trip_id=trip.id,
        source_trigger_id=trigger.id,
        suggestion_type=candidate.suggestion_type,
        status=status,
        title=candidate.title,
        rationale=candidate.rationale,
        evidence=candidate.evidence,
        proposed_action=candidate.proposed_action,
        confidence=confidence,
        requires_approval=candidate.requires_approval,
        severity=candidate.severity,
        dedupe_key=candidate.dedupe_key,
        push_status=push_status,
        push_reason=push_reason,
        notified_at=now if push_status == "PUSHED" else None,
        feedback_penalty=penalty,
    )
    session.add(suggestion)
    session.flush()
    evidence_service.record_many(
        session,
        organization_id,
        subject_type="proactive_suggestion",
        subject_id=suggestion.id,
        items=candidate.evidence,
        source=f"proactive:{candidate.rule_id}",
    )

    _audit(
        session,
        organization_id,
        suggestion,
        "DETECTED",
        detail={
            "trigger_id": str(trigger.id),
            "rule_id": candidate.rule_id,
            "trigger_type": candidate.trigger_type,
        },
    )
    _audit(
        session,
        organization_id,
        suggestion,
        "EVALUATED",
        detail={
            "base_confidence": candidate.confidence,
            "feedback_penalty": penalty,
            "confidence": confidence,
            "in_cooldown": in_cooldown,
            "push_status": push_status,
            "push_reason": push_reason,
        },
    )
    _audit(
        session,
        organization_id,
        suggestion,
        "SUPPRESSED" if status == "SUPPRESSED" else "PROPOSED",
        detail={"push_status": push_status, "push_reason": push_reason},
    )

    trigger.status = "SUPPRESSED" if status == "SUPPRESSED" else "PROPOSED"
    trigger.suggestion_id = suggestion.id
    trigger.detail = {
        "suggestion_id": str(suggestion.id),
        "confidence": confidence,
        "push_status": push_status,
        "push_reason": push_reason,
    }

    # 事件与建议同事务：通知消费者据此入队。抑制/保留的建议也会发事件（审计完整），
    # 但消费者只在 ``push_status=PUSHED`` 时创建通知。
    outbox_service.emit(
        session,
        organization_id,
        aggregate_type=outbox_service.AGGREGATE_TRIP,
        aggregate_id=trip.id,
        event_type=outbox_service.EVENT_SUGGESTION_PROPOSED,
        payload={
            "suggestion_id": str(suggestion.id),
            "trip_id": str(trip.id),
            "push_status": push_status,
            "suggestion_type": suggestion.suggestion_type,
        },
        consumers=(outbox_service.CONSUMER_NOTIFICATION_DISPATCH,),
        dedupe_key=f"suggestion-proposed:{suggestion.id}",
    )

    report.suggestions += 1
    if status == "SUPPRESSED":
        report.suppressed += 1
    elif push_status == "PUSHED":
        report.pushed += 1
    else:
        report.withheld += 1


def _claim_trigger(
    session: Session,
    organization_id: UUID,
    trip: Trip,
    candidate: Candidate,
    report: ScanReport,
) -> ProactiveTrigger | None:
    """登记触发。返回 ``None`` 表示这条触发此前已处理过（本轮没有新工作）。

    用 ``ON CONFLICT DO NOTHING`` 而不是"先查后写"：并发扫描下后者会双双查不到、
    双双插入，然后由唯一约束把其中一个变成 500。数据库裁决才是对的。
    """
    inserted_id = session.execute(
        pg_insert(ProactiveTrigger.__table__)
        .values(
            organization_id=organization_id,
            trip_id=trip.id,
            type=candidate.trigger_type,
            rule_id=candidate.rule_id,
            dedupe_key=candidate.dedupe_key,
            payload=candidate.trigger_payload,
            status="DETECTED",
            detail={},
        )
        .on_conflict_do_nothing(constraint="uq_proactive_triggers_org_trip_rule_dedupe")
        .returning(ProactiveTrigger.__table__.c.id)
    ).scalar_one_or_none()

    if inserted_id is None:
        existing = session.scalars(
            select(ProactiveTrigger)
            .where(
                ProactiveTrigger.organization_id == organization_id,
                ProactiveTrigger.trip_id == trip.id,
                ProactiveTrigger.type == candidate.trigger_type,
                ProactiveTrigger.rule_id == candidate.rule_id,
                ProactiveTrigger.dedupe_key == candidate.dedupe_key,
            )
            .with_for_update()
        ).one_or_none()
        if existing is None:
            # 极小概率：冲突后对手事务回滚了。当作本轮已处理，下一轮会重新登记。
            report.deduped += 1
            return None
        if existing.status != "FAILED":
            report.deduped += 1
            return None
        # 上次评估失败：允许重试，但仍然复用同一条触发记录。
        existing.status = "DETECTED"
        existing.detail = {}
        session.flush()
        return existing

    report.triggers += 1
    trigger = session.get(ProactiveTrigger, inserted_id)
    if trigger is None:  # pragma: no cover —— RETURNING 之后同事务内必然可见
        raise RuntimeError("触发记录插入后不可见")
    return trigger


def _feedback_penalty(
    session: Session, organization_id: UUID, suggestion_type: str, now: datetime
) -> float:
    """误报回收：同组织同类型被驳回的次数越多，新建议的置信度越低（上限见配置）。"""
    settings = get_settings()
    window_start = now - timedelta(days=settings.proactive_feedback_window_days)
    dismissed = session.scalar(
        select(func.count())
        .select_from(ProactiveSuggestion)
        .where(
            ProactiveSuggestion.organization_id == organization_id,
            ProactiveSuggestion.suggestion_type == suggestion_type,
            ProactiveSuggestion.status == "DISMISSED",
            ProactiveSuggestion.dismissed_at >= window_start,
        )
    ) or 0
    return round(
        min(settings.proactive_max_feedback_penalty, dismissed * settings.proactive_dismissal_penalty),
        3,
    )


def _daily_cap_reached(
    session: Session, organization_id: UUID, owner_user_id: UUID | None, now: datetime
) -> bool:
    """每出差人每日推送上限。没有 owner 的出行无法归属到人，不套用上限（并保持可解释）。"""
    if owner_user_id is None:
        return False
    settings = get_settings()
    start_of_day = session.scalar(select(func.date_trunc("day", now)))
    used = session.scalar(
        select(func.count())
        .select_from(ProactiveSuggestion)
        .join(Trip, Trip.id == ProactiveSuggestion.trip_id)
        .where(
            ProactiveSuggestion.organization_id == organization_id,
            ProactiveSuggestion.push_status == "PUSHED",
            ProactiveSuggestion.notified_at >= start_of_day,
            Trip.owner_user_id == owner_user_id,
        )
    ) or 0
    return used >= settings.daily_suggestion_cap


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------
def list_suggestions(
    session: Session,
    organization_id: UUID | str,
    *,
    trip_id: UUID | str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[ProactiveSuggestion]:
    """用户可见的建议流。

    默认排除两类内部状态：``SUPPRESSED``（冷却抑制）与 ``WITHHELD``（低置信度/超上限）。
    它们留在库里供指标与复盘，但不该出现在"今天要处理什么"的列表里。
    """
    stmt = select(ProactiveSuggestion).where(
        ProactiveSuggestion.organization_id == str(organization_id),
        ProactiveSuggestion.status != "SUPPRESSED",
        ProactiveSuggestion.push_status != "WITHHELD",
    )
    if trip_id is not None:
        stmt = stmt.where(ProactiveSuggestion.trip_id == str(trip_id))
    if status is not None:
        stmt = stmt.where(ProactiveSuggestion.status == status.upper())
    stmt = (
        stmt.order_by(ProactiveSuggestion.created_at.desc(), ProactiveSuggestion.id)
        .limit(limit)
        .offset(offset)
    )
    return list(session.scalars(stmt).all())


def daily_cap_usage(
    session: Session, organization_id: UUID | str, owner_user_id: UUID | str | None
) -> int:
    """今天已推送给该出差人的建议条数（与扫描时的上限判定同一口径）。"""
    if owner_user_id is None:
        return 0
    now = session.scalar(select(func.clock_timestamp()))
    start_of_day = session.scalar(select(func.date_trunc("day", now)))
    return int(
        session.scalar(
            select(func.count())
            .select_from(ProactiveSuggestion)
            .join(Trip, Trip.id == ProactiveSuggestion.trip_id)
            .where(
                ProactiveSuggestion.organization_id == str(organization_id),
                ProactiveSuggestion.push_status == "PUSHED",
                ProactiveSuggestion.notified_at >= start_of_day,
                Trip.owner_user_id == str(owner_user_id),
            )
        )
        or 0
    )


def get_suggestion(
    session: Session, organization_id: UUID | str, suggestion_id: UUID | str
) -> ProactiveSuggestion:
    suggestion = session.scalars(
        select(ProactiveSuggestion).where(
            ProactiveSuggestion.organization_id == str(organization_id),
            ProactiveSuggestion.id == str(suggestion_id),
        )
    ).one_or_none()
    if suggestion is None:
        raise NotFound("主动建议不存在", details={"suggestion_id": str(suggestion_id)})
    return suggestion


# ---------------------------------------------------------------------------
# 人审与受控执行
# ---------------------------------------------------------------------------
def approve(
    session: Session,
    organization_id: UUID | str,
    suggestion_id: UUID | str,
    *,
    actor_user_id: UUID | str,
    actor_role: str | None = None,
    now: datetime | None = None,
) -> ApprovalOutcome:
    """人审通过 → 会签统计 → 达到阈值后受控执行 → 审计。

    多人会签：每个审批人一条 ``approvals``；不同审批人数量达到策略
    ``required_approvals`` 才执行。未达阈值时建议保持 ``PENDING``，客户端可继续等待其他审批人。
    同一审批人重复批准是幂等的，不会重复计数。
    """
    current = now or session.scalar(select(func.clock_timestamp()))
    suggestion = session.scalars(
        select(ProactiveSuggestion)
        .where(
            ProactiveSuggestion.organization_id == str(organization_id),
            ProactiveSuggestion.id == str(suggestion_id),
        )
        .with_for_update()
    ).one_or_none()
    if suggestion is None:
        raise NotFound("主动建议不存在", details={"suggestion_id": str(suggestion_id)})

    approvals = list(
        session.scalars(
            select(Approval).where(
                Approval.organization_id == str(organization_id),
                Approval.subject_type == APPROVAL_SUBJECT_SUGGESTION,
                Approval.subject_id == suggestion.id,
                Approval.decision == "APPROVED",
            )
        )
    )
    approval_count = len({str(item.decided_by_user_id) for item in approvals})
    required_approvals = approval_policy_service.enforce(
        session,
        organization_id,
        severity=suggestion.severity,
        actor_role=actor_role,
    )

    if suggestion.status in ("APPROVED", "EXECUTED"):
        return ApprovalOutcome(
            suggestion=suggestion,
            approval=approvals[0] if approvals else _missing_approval(suggestion, actor_user_id, current),
            executed=True,
            approval_count=max(approval_count, 1),
            required_approvals=required_approvals,
        )

    if suggestion.status != "PENDING":
        raise Conflict(
            "该建议当前不可批准",
            details={"suggestion_id": str(suggestion.id), "status": suggestion.status},
        )

    executor = EXECUTORS.get(suggestion.suggestion_type)
    if executor is None:
        raise Unprocessable(
            "该建议类型没有登记执行器，不能执行",
            details={"suggestion_type": suggestion.suggestion_type},
        )

    actor = str(actor_user_id)
    approval = next(
        (item for item in approvals if str(item.decided_by_user_id) == actor), None
    )
    if approval is None:
        approval = Approval(
            organization_id=str(organization_id),
            subject_type=APPROVAL_SUBJECT_SUGGESTION,
            subject_id=suggestion.id,
            decision="APPROVED",
            decided_by_user_id=actor,
            decided_at=current,
            note="由 POST /v1/proactive/suggestions/{id}:approve 记录",
        )
        session.add(approval)
        session.flush()
        approvals.append(approval)
        approval_count += 1

    _audit(
        session,
        organization_id,
        suggestion,
        "APPROVED",
        actor_type="USER",
        actor_user_id=actor_user_id,
        detail={
            "approval_id": str(approval.id),
            "approval_count": approval_count,
            "required_approvals": required_approvals,
        },
    )

    if approval_count < required_approvals:
        session.flush()
        return ApprovalOutcome(
            suggestion=suggestion,
            approval=approval,
            executed=False,
            approval_count=approval_count,
            required_approvals=required_approvals,
        )

    suggestion.status = "APPROVED"
    suggestion.approved_by_user_id = actor
    suggestion.approved_at = current

    trip = session.get(Trip, suggestion.trip_id)
    result = executor(session, suggestion, trip=trip, now=current)
    suggestion.execution_result = result
    suggestion.executed_at = current
    suggestion.status = "EXECUTED"
    from app.models.workflow import ExternalAction

    session.add(
        ExternalAction(
            organization_id=str(organization_id),
            subject_type="proactive_suggestion",
            subject_id=suggestion.id,
            action_type=str((suggestion.proposed_action or {}).get("kind") or suggestion.suggestion_type),
            status="PREPARED",
            request=dict(suggestion.proposed_action or {}),
            response=result,
            requires_approval=suggestion.requires_approval,
            approved_by_user_id=actor,
            executed_at=current,
        )
    )
    _audit(
        session,
        organization_id,
        suggestion,
        "EXECUTED",
        detail={"result": result},
    )
    _audit(
        session,
        organization_id,
        suggestion,
        "AUDITED",
        detail={"note": "执行结果已留痕；对外动作仍由业务服务按授权另行执行"},
    )
    session.flush()
    return ApprovalOutcome(
        suggestion=suggestion,
        approval=approval,
        executed=True,
        approval_count=approval_count,
        required_approvals=required_approvals,
    )


def _missing_approval(
    suggestion: ProactiveSuggestion, actor_user_id: UUID | str, now: datetime
) -> Approval:
    """为历史/异常数据补齐一个只读占位，避免响应因缺审批而 500。"""
    return Approval(
        organization_id=str(suggestion.organization_id),
        subject_type=APPROVAL_SUBJECT_SUGGESTION,
        subject_id=suggestion.id,
        decision="APPROVED",
        decided_by_user_id=str(actor_user_id),
        decided_at=now,
        note="审批记录缺失时的响应占位（未落库）",
    )


def dismiss(
    session: Session,
    organization_id: UUID | str,
    suggestion_id: UUID | str,
    *,
    actor_user_id: UUID | str,
    reason: str | None = None,
    now: datetime | None = None,
) -> DismissOutcome:
    """驳回建议：标记 ``DISMISSED``，并把负反馈折算成同类建议的置信度折扣（AT-P04）。"""
    current = now or session.scalar(select(func.clock_timestamp()))
    suggestion = session.scalars(
        select(ProactiveSuggestion)
        .where(
            ProactiveSuggestion.organization_id == str(organization_id),
            ProactiveSuggestion.id == str(suggestion_id),
        )
        .with_for_update()
    ).one_or_none()
    if suggestion is None:
        raise NotFound("主动建议不存在", details={"suggestion_id": str(suggestion_id)})

    if suggestion.status == "DISMISSED":
        # 重复驳回是幂等的：不再写第二条审计，但把当前反馈强度回给客户端。
        return DismissOutcome(
            suggestion=suggestion,
            weight_delta=-_feedback_penalty(
                session, UUID(str(organization_id)), suggestion.suggestion_type, current
            ),
        )

    if suggestion.status != "PENDING":
        raise Conflict(
            "该建议当前不可驳回",
            details={"suggestion_id": str(suggestion.id), "status": suggestion.status},
        )

    suggestion.status = "DISMISSED"
    suggestion.dismissed_at = current
    suggestion.dismissed_reason = reason
    _audit(
        session,
        organization_id,
        suggestion,
        "DISMISSED",
        actor_type="USER",
        actor_user_id=actor_user_id,
        detail={"reason": reason},
    )
    session.flush()

    penalty = _feedback_penalty(session, UUID(str(organization_id)), suggestion.suggestion_type, current)
    return DismissOutcome(suggestion=suggestion, weight_delta=-penalty)


# ---------------------------------------------------------------------------
# 执行器：只准备，不对外
# ---------------------------------------------------------------------------
Executor = Callable[..., dict[str, Any]]


def _execute_visa(
    session: Session, suggestion: ProactiveSuggestion, *, trip: Trip | None, now: datetime
) -> dict[str, Any]:
    catalog = _visa_catalog()
    countries = [str(code).upper() for code in (suggestion.proposed_action.get("params") or {}).get("countries", [])]
    if trip is not None and countries:
        cases = visa_service.ensure_cases(
            session,
            trip.organization_id,
            trip,
            countries=countries,
            actor_user_id=suggestion.approved_by_user_id,
        )
        case_ids = [str(case.id) for case in cases]
    else:
        case_ids = []
    checklist: list[dict[str, Any]] = []
    for code in countries:
        entry = catalog["countries"].get(code)
        if entry is None:
            checklist.append(
                {
                    "country": code,
                    "status": "MANUAL_REVIEW_REQUIRED",
                    "detail": "本地目录未收录，请直接查询官方渠道",
                }
            )
            continue
        checklist.append(
            {
                "country": code,
                "label": entry["label"],
                "entry_type": entry["business_entry"],
                "status": "NEEDS_HUMAN_CONFIRMATION",
                "detail": entry["review_hint"],
            }
        )
    return {
        "status": "PREPARED",
        "detail": "已生成签证/证件核对清单（草稿），未向任何外部机构提交",
        "catalog_version": catalog["version"],
        "checklist": checklist,
        "visa_case_ids": case_ids,
    }


def _execute_geocoding(
    session: Session, suggestion: ProactiveSuggestion, *, trip: Trip | None, now: datetime
) -> dict[str, Any]:
    place_ids = list((suggestion.proposed_action.get("params") or {}).get("place_ids", []))
    return {
        "status": "PREPARED",
        "detail": "已生成坐标补全任务（草稿），未调用外部地理编码服务",
        "place_ids": place_ids,
    }


def _execute_plan_adjustment(
    session: Session, suggestion: ProactiveSuggestion, *, trip: Trip | None, now: datetime
) -> dict[str, Any]:
    params = suggestion.proposed_action.get("params") or {}
    return {
        "status": "PREPARED",
        "detail": "已生成计划调整建议（草稿），请在计划工作区确认后再生成新版本",
        "plan_version_id": params.get("plan_version_id"),
        "visit_ids": list(params.get("visit_ids", [])),
    }


def _execute_external_review(
    session: Session, suggestion: ProactiveSuggestion, *, trip: Trip | None, now: datetime
) -> dict[str, Any]:
    params = suggestion.proposed_action.get("params") or {}
    return {
        "status": "PREPARED",
        "detail": "已整理外部变化摘要（草稿）；请按官方渠道复核后再调整行程，未对外发送或改签",
        "signal_id": params.get("signal_id"),
        "signal_type": params.get("signal_type"),
        "subject_ref": params.get("subject_ref"),
        "impact_assessment": params.get("impact_assessment"),
    }


EXECUTORS: dict[str, Executor] = {
    SUGGESTION_VISA_REMINDER: _execute_visa,
    SUGGESTION_PLACE_COORDINATES: _execute_geocoding,
    SUGGESTION_PLAN_UNPLACED: _execute_plan_adjustment,
    SUGGESTION_VISA_POLICY: _execute_external_review,
    SUGGESTION_WEATHER: _execute_external_review,
    SUGGESTION_FX: _execute_external_review,
    SUGGESTION_FLIGHT: _execute_external_review,
}


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------
def _audit(
    session: Session,
    organization_id: UUID | str,
    suggestion: ProactiveSuggestion,
    action: str,
    *,
    actor_type: str = "SYSTEM",
    actor_user_id: UUID | str | None = None,
    detail: dict[str, Any] | None = None,
) -> SuggestionAudit:
    # 用 ``clock_timestamp()`` 而不是列默认的 ``now()``：后者在一个事务内恒定，同一轮
    # 扫描写下的 DETECTED/EVALUATED/PROPOSED 会得到完全相同的时间戳，审计链就失去了顺序。
    row = SuggestionAudit(
        organization_id=str(organization_id),
        suggestion_id=suggestion.id,
        action=action,
        actor_type=actor_type,
        actor_user_id=None if actor_user_id is None else str(actor_user_id),
        detail=detail or {},
        created_at=session.scalar(select(func.clock_timestamp())),
    )
    session.add(row)
    return row


def _visa_catalog() -> dict[str, Any]:
    from app.services.visa_catalog import load_catalog

    return load_catalog()


__all__ = [
    "APPROVAL_SUBJECT_SUGGESTION",
    "ApprovalOutcome",
    "Candidate",
    "DismissOutcome",
    "EXECUTORS",
    "RULES",
    "SUGGESTION_PLAN_UNPLACED",
    "SUGGESTION_VISA_POLICY",
    "SUGGESTION_WEATHER",
    "SUGGESTION_FX",
    "SUGGESTION_FLIGHT",
    "SUGGESTION_PLACE_COORDINATES",
    "SUGGESTION_VISA_REMINDER",
    "TRIGGER_EXTERNAL",
    "ScanReport",
    "approve",
    "daily_cap_usage",
    "dismiss",
    "get_suggestion",
    "list_suggestions",
    "scan_organization",
    "scan_trip",
]
