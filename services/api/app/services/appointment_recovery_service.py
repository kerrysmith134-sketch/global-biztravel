"""Deterministic, evidence-bound recovery planning for disrupted customer appointments.

The planner translates an already verified flight-impact snapshot into reviewable
next steps. It never invents customer availability or candidate appointment times,
and it never changes a PlanVersion, booking, Visit, or external communication state.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

AGENT_TYPE = "APPOINTMENT_RECOVERY"
AGENT_VERSION = "appointment-recovery-v1"
SCHEMA_VERSION = "1.0"


def _action_for_stop(stop: dict[str, Any]) -> dict[str, Any] | None:
    status = str(stop.get("appointment_status") or "UNASSESSED")
    raw_delay = stop.get("delay_minutes")
    delay = max(0, int(raw_delay or 0))
    common: dict[str, Any] = {
        "visit_id": stop.get("visit_id"),
        "plan_stop_id": stop.get("plan_stop_id"),
        "planned_arrival_at": stop.get("planned_arrival_at"),
        "projected_arrival_at": stop.get("projected_arrival_at"),
        "delay_minutes": delay if raw_delay is not None else None,
        "appointment_status": status,
        "time_window_end": stop.get("time_window_end"),
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
        return {
            **common,
            "action_code": "REVALIDATE_TRAVEL_CHAIN",
            "priority": "MEDIUM",
            "decision_status": "DATA_REVALIDATION_REQUIRED",
            "rationale": "后续影响传播因交通段缺失、过期或计划时序不一致而停止。",
            "required_inputs": [
                "FRESH_NON_STALE_TRAVEL_LEG",
                "CONSISTENT_PLAN_TIMELINE",
            ],
            "recommended_steps": [
                "补齐或刷新相邻交通段估算",
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
    if delay > 0 and status == "WITHIN_WINDOW":
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


def build_recovery_brief(
    impact_assessment: dict[str, Any],
    *,
    generated_at: datetime,
) -> dict[str, Any]:
    """Build a deterministic recovery brief from the verified impact snapshot only."""
    stop_impacts = impact_assessment.get("stop_impacts")
    if not isinstance(stop_impacts, list):
        stop_impacts = []
    actions: list[dict[str, Any]] = []
    for stop in stop_impacts:
        if isinstance(stop, dict):
            action = _action_for_stop(stop)
            if action is not None:
                actions.append(action)

    if not actions:
        status = "NO_RECOVERY_ACTION_REQUIRED"
    elif any(item["priority"] == "HIGH" for item in actions):
        status = "RECOVERY_REQUIRED"
    else:
        status = "REVIEW_REQUIRED"

    source = {
        "signal_id": impact_assessment.get("signal_id"),
        "operational_update_id": impact_assessment.get("operational_update_id"),
        "provider": impact_assessment.get("provider"),
        "provider_event_id": impact_assessment.get("provider_event_id"),
        "content_sha256": impact_assessment.get("content_sha256"),
        "update_type": impact_assessment.get("update_type"),
        "flight_segment_id": impact_assessment.get("flight_segment_id"),
        "plan_version_id": impact_assessment.get("plan_version_id"),
        "binding_id": impact_assessment.get("binding_id"),
    }
    data_gaps = sorted({
        required
        for action in actions
        for required in action.get("required_inputs", [])
    })
    return {
        "agent_type": AGENT_TYPE,
        "agent_version": AGENT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "execution_mode": "DETERMINISTIC",
        "status": status,
        "generated_at": generated_at.isoformat(),
        "source": source,
        "impact_status": impact_assessment.get("status"),
        "propagation_complete": bool(impact_assessment.get("propagation_complete", False)),
        "affected_visit_ids": list(impact_assessment.get("affected_visit_ids") or []),
        "missed_visit_ids": list(impact_assessment.get("missed_visit_ids") or []),
        "unassessed_visit_ids": list(impact_assessment.get("unassessed_visit_ids") or []),
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
