"""Deterministic, evidence-bound recovery planning for disrupted customer appointments.

The planner translates an already verified flight-impact snapshot into reviewable
next steps. Incomplete or inconsistent impact snapshots always fail closed to review.
It never invents customer availability or changes bookings, plans, or external state.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

AGENT_TYPE = "APPOINTMENT_RECOVERY"
AGENT_VERSION = "appointment-recovery-v1.1"
SCHEMA_VERSION = "1.1"

_APPOINTMENT_STATUSES = {
    "MISSED_WINDOW",
    "IMPACT_UNDETERMINED",
    "UNASSESSED",
    "WINDOW_END_UNKNOWN",
    "WITHIN_WINDOW",
}


def _string_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(
        item.strip() for item in value
        if isinstance(item, str) and item.strip()
    ))


def _action_for_stop(stop: dict[str, Any]) -> dict[str, Any] | None:
    raw_status = stop.get("appointment_status")
    status = raw_status if isinstance(raw_status, str) else "UNASSESSED"
    reason = stop.get("reason")
    quality_gap: str | None = None

    if status not in _APPOINTMENT_STATUSES:
        quality_gap = "UNRECOGNIZED_APPOINTMENT_STATUS"
        status = "UNASSESSED"

    raw_delay = stop.get("delay_minutes")
    delay: int | None = None
    if raw_delay is not None:
        if (
            isinstance(raw_delay, int)
            and not isinstance(raw_delay, bool)
            and raw_delay >= 0
        ):
            delay = raw_delay
        elif (
            isinstance(raw_delay, float)
            and raw_delay >= 0
            and raw_delay.is_integer()
        ):
            delay = int(raw_delay)
        else:
            quality_gap = "INVALID_DELAY_VALUE"
            status = "UNASSESSED"

    if status == "WITHIN_WINDOW" and delay is None:
        quality_gap = quality_gap or "WITHIN_WINDOW_DELAY_MISSING"
        status = "UNASSESSED"

    common: dict[str, Any] = {
        "scope": "VISIT",
        "visit_id": stop.get("visit_id"),
        "plan_stop_id": stop.get("plan_stop_id"),
        "planned_arrival_at": stop.get("planned_arrival_at"),
        "projected_arrival_at": stop.get("projected_arrival_at"),
        "delay_minutes": delay,
        "appointment_status": status,
        "time_window_end": stop.get("time_window_end"),
        "assessment_reason": reason,
        "candidate_windows": [],
        "customer_message": {
            "status": "NOT_DRAFTED",
            "reason": "尚未确认客户联系人、沟通语言及客户可用时段",
        },
    }

    if status == "MISSED_WINDOW":
        return {
            **common,
            "action_code": "REQUEST_CUSTOMER_RESCHEDULE_WINDOW",
            "priority": "HIGH",
            "decision_status": "REQUIRES_HUMAN_DECISION",
            "rationale": "预计到达晚于预约窗口结束时间；系统不能自行替客户指定新时段。",
            "required_inputs": [
                "CUSTOMER_CONFIRMED_ALTERNATIVE_WINDOW",
                "CUSTOMER_TIMEZONE",
                "TRAVELER_APPROVAL",
            ],
            "recommended_steps": [
                "由出差人确认是否保留该客户拜访",
                "向客户征求可选日期与时间窗口（仅准备草稿，不自动发送）",
                "收到客户确认后再生成新计划并展示版本差异",
            ],
        }
    if status == "IMPACT_UNDETERMINED":
        return {
            **common,
            "action_code": "VERIFY_REPLACEMENT_FLIGHT_AND_RECONFIRM",
            "priority": "HIGH",
            "decision_status": "REQUIRES_HUMAN_DECISION",
            "rationale": "航班取消或备降后没有可信的目的地到达时间，预约影响尚不能确定。",
            "required_inputs": [
                "CONFIRMED_REPLACEMENT_FLIGHT_OR_ARRIVAL",
                "CURRENT_GROUND_TRANSFER_ESTIMATE",
                "TRAVELER_APPROVAL",
            ],
            "recommended_steps": [
                "核实替代航班或最终到达机场与时间",
                "重新获取到客户地点的有效交通估算",
                "证据齐全后重新评估预约，不把取消或备降换算成延误分钟数",
            ],
        }
    if status == "UNASSESSED":
        required_inputs = [
            "FRESH_NON_STALE_TRAVEL_LEG",
            "CONSISTENT_PLAN_TIMELINE",
        ]
        if quality_gap:
            required_inputs.append(quality_gap)
        return {
            **common,
            "action_code": "REVALIDATE_TRAVEL_CHAIN",
            "priority": "MEDIUM",
            "decision_status": "DATA_REVALIDATION_REQUIRED",
            "rationale": (
                "影响数据缺失、格式异常或状态未知；系统不能把无法评估解释为没有预约影响。"
            ),
            "required_inputs": required_inputs,
            "recommended_steps": [
                "补齐或刷新相邻交通段估算与预约数据",
                "重新运行确定性影响评估",
                "重新评估前不向客户宣称已错过预约",
            ],
        }
    if status == "WINDOW_END_UNKNOWN":
        return {
            **common,
            "action_code": "CONFIRM_CUSTOMER_APPOINTMENT_WINDOW",
            "priority": "MEDIUM",
            "decision_status": "CUSTOMER_CONFIRMATION_REQUIRED",
            "rationale": "拜访缺少预约窗口结束时间，无法确定延误是否导致错过。",
            "required_inputs": [
                "CUSTOMER_CONFIRMED_APPOINTMENT_WINDOW",
                "CUSTOMER_TIMEZONE",
            ],
            "recommended_steps": [
                "确认客户预约时间窗口及当地时区",
                "重新计算预计到达与预约窗口的关系",
            ],
        }
    if delay is not None and delay > 0 and status == "WITHIN_WINDOW":
        return {
            **common,
            "action_code": "RECONFIRM_ARRIVAL_AND_KEEP_APPOINTMENT_PROVISIONAL",
            "priority": "MEDIUM",
            "decision_status": "AT_RISK_REVIEW",
            "rationale": "当前计算仍在预约窗口内，但计划余量已被消耗；这不是已错过预约的结论。",
            "required_inputs": [
                "LATEST_CONFIRMED_ARRIVAL",
                "CURRENT_GROUND_TRANSFER_ESTIMATE",
                "TRAVELER_DECISION",
            ],
            "recommended_steps": [
                "核对最新航班到达时间",
                "核对落地后交通是否仍可行",
                "证据更新前保留预约，不自动取消或改期",
            ],
        }
    return None


def _trip_level_revalidation_action(
    impact_status: Any,
    reason: str,
) -> dict[str, Any]:
    status_label = (
        impact_status.strip()
        if isinstance(impact_status, str) and impact_status.strip()
        else "UNKNOWN"
    )
    return {
        "scope": "TRIP",
        "visit_id": None,
        "plan_stop_id": None,
        "action_code": "REVALIDATE_FLIGHT_PLAN_IMPACT_ASSESSMENT",
        "priority": "MEDIUM",
        "decision_status": "DATA_REVALIDATION_REQUIRED",
        "appointment_status": "UNASSESSED",
        "assessment_status": status_label,
        "assessment_reason": reason,
        "planned_arrival_at": None,
        "projected_arrival_at": None,
        "delay_minutes": None,
        "candidate_windows": [],
        "customer_message": {
            "status": "NOT_DRAFTED",
            "reason": "影响评估尚不完整，不能生成客户沟通内容",
        },
        "rationale": (
            "航班事实虽已进入评估，但当前 ACTIVE 计划、航段绑定或站点影响快照尚未形成"
            "完整可验证链路；必须先修复数据关联并重新评估。"
        ),
        "required_inputs": [
            "CURRENT_ACTIVE_PLAN",
            "VERIFIED_FLIGHT_SEGMENT_BINDING",
            "COMPLETE_STOP_IMPACT_SNAPSHOT",
        ],
        "recommended_steps": [
            "确认当前出行的 ACTIVE PlanVersion",
            "核验机票段与当前计划 AIR 交通段的显式绑定",
            "重新运行航班到预约的完整影响评估",
        ],
    }


def build_recovery_brief(
    impact_assessment: dict[str, Any],
    *,
    generated_at: datetime,
) -> dict[str, Any]:
    """Build a deterministic recovery brief; incomplete inputs must never yield a clean bill."""
    assessment = impact_assessment if isinstance(impact_assessment, dict) else {}
    raw_stop_impacts = assessment.get("stop_impacts")
    stop_impacts = raw_stop_impacts if isinstance(raw_stop_impacts, list) else []
    data_quality_gaps: set[str] = set()
    if not isinstance(raw_stop_impacts, list):
        data_quality_gaps.add("STOP_IMPACT_SNAPSHOT_MISSING_OR_INVALID")
    for key in ("affected_visit_ids", "missed_visit_ids", "unassessed_visit_ids"):
        value = assessment.get(key)
        if value is not None and not isinstance(value, list):
            data_quality_gaps.add("INVALID_VISIT_ID_COLLECTION")

    propagation_complete = assessment.get("propagation_complete") is True
    reported_unassessed = _string_ids(assessment.get("unassessed_visit_ids"))
    if reported_unassessed:
        propagation_complete = False

    expected_visit_ids = set(
        _string_ids(assessment.get("affected_visit_ids"))
        + _string_ids(assessment.get("missed_visit_ids"))
        + reported_unassessed
    )
    rows_by_visit: dict[str, dict[str, Any]] = {}
    conflicting_visit_ids: set[str] = set()
    for stop in stop_impacts:
        if not isinstance(stop, dict):
            data_quality_gaps.add("INVALID_STOP_IMPACT_ROW")
            continue
        visit_id = stop.get("visit_id")
        if not isinstance(visit_id, str) or not visit_id.strip():
            data_quality_gaps.add("STOP_IMPACT_VISIT_ID_MISSING")
            continue
        visit_id = visit_id.strip()
        expected_visit_ids.add(visit_id)
        existing = rows_by_visit.get(visit_id)
        if existing is None:
            rows_by_visit[visit_id] = stop
        elif existing != stop:
            conflicting_visit_ids.add(visit_id)
            data_quality_gaps.add("CONFLICTING_STOP_IMPACT_ROWS")

    for visit_id in conflicting_visit_ids:
        rows_by_visit[visit_id] = {
            "visit_id": visit_id,
            "appointment_status": "UNASSESSED",
            "reason": "CONFLICTING_STOP_IMPACT_ROWS",
        }

    missing_visit_ids = expected_visit_ids - set(rows_by_visit)
    if missing_visit_ids:
        data_quality_gaps.add("STOP_IMPACT_ROW_MISSING")
        propagation_complete = False
        for visit_id in sorted(missing_visit_ids):
            rows_by_visit[visit_id] = {
                "visit_id": visit_id,
                "appointment_status": "UNASSESSED",
                "reason": "STOP_IMPACT_ROW_MISSING",
            }

    actions: list[dict[str, Any]] = []
    for visit_id in sorted(rows_by_visit):
        action = _action_for_stop(rows_by_visit[visit_id])
        if action is not None:
            actions.append(action)
            if action["appointment_status"] == "UNASSESSED":
                data_quality_gaps.add(
                    str(rows_by_visit[visit_id].get("reason") or "STOP_IMPACT_UNASSESSED")
                )
    if any(action["action_code"] == "REVALIDATE_TRAVEL_CHAIN" for action in actions):
        propagation_complete = False
    if data_quality_gaps:
        propagation_complete = False

    has_revalidation = any(
        action["action_code"] in {
            "REVALIDATE_TRAVEL_CHAIN",
            "REVALIDATE_FLIGHT_PLAN_IMPACT_ASSESSMENT",
        }
        for action in actions
    )
    if not propagation_complete and not has_revalidation:
        actions.append(
            _trip_level_revalidation_action(
                assessment.get("status"),
                "IMPACT_PROPAGATION_INCOMPLETE",
            )
        )

    if not actions and propagation_complete:
        status = "NO_RECOVERY_ACTION_REQUIRED"
    elif any(item["priority"] == "HIGH" for item in actions):
        status = "RECOVERY_REQUIRED"
    else:
        status = "REVIEW_REQUIRED"

    source = {
        "signal_id": assessment.get("signal_id"),
        "operational_update_id": assessment.get("operational_update_id"),
        "provider": assessment.get("provider"),
        "provider_event_id": assessment.get("provider_event_id"),
        "content_sha256": assessment.get("content_sha256"),
        "update_type": assessment.get("update_type"),
        "flight_segment_id": assessment.get("flight_segment_id"),
        "plan_version_id": assessment.get("plan_version_id"),
        "trip_version": assessment.get("trip_version"),
        "plan_trip_version": assessment.get("plan_trip_version"),
        "plan_input_current": assessment.get("plan_input_current"),
        "binding_id": assessment.get("binding_id"),
    }
    unassessed_visit_ids = sorted(
        set(reported_unassessed)
        | {
            str(action["visit_id"])
            for action in actions
            if action.get("appointment_status") == "UNASSESSED"
            and action.get("visit_id")
        }
    )
    data_gaps = sorted(
        data_quality_gaps
        | {
            required
            for action in actions
            for required in action.get("required_inputs", [])
        }
        | ({"IMPACT_PROPAGATION_INCOMPLETE"} if not propagation_complete else set())
    )
    return {
        "agent_type": AGENT_TYPE,
        "agent_version": AGENT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "execution_mode": "DETERMINISTIC",
        "status": status,
        "generated_at": generated_at.isoformat(),
        "source": source,
        "impact_status": assessment.get("status"),
        "propagation_complete": propagation_complete,
        "impact_snapshot_complete": propagation_complete and not data_quality_gaps,
        "affected_visit_ids": _string_ids(assessment.get("affected_visit_ids")),
        "missed_visit_ids": _string_ids(assessment.get("missed_visit_ids")),
        "unassessed_visit_ids": unassessed_visit_ids,
        "actions": actions,
        "data_gaps": data_gaps,
        "guardrails": {
            "human_decision_required": bool(actions),
            "plan_mutated": False,
            "booking_changed": False,
            "customer_contacted": False,
            "customer_message_sent": False,
            "candidate_windows_inferred": False,
            "candidate_windows": [],
        },
    }


__all__ = ["AGENT_TYPE", "AGENT_VERSION", "SCHEMA_VERSION", "build_recovery_brief"]
