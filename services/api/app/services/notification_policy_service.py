"""通知等级策略（PRD S8-08「NotificationPolicyEngine」）。

主动系统不能"每条都一样响"。等级在**入队时**判定并随通知落库，理由：

- 等级决定展示方式（P0/P1 立即可见并进待办；P2 汇总；P3 只留在工作区），
  事后重算会让"当时为什么这么推"无法回放；
- 分类只依赖**本地已知事实**（建议类型与风险等级），不猜测尚未接入的外部数据。

确定性规则（按顺序命中即停）：

1. **P0 紧急**：关键交通中断或重要预约可能失败——航班状态告警、已生成但关键拜访未排入，
   且风险为 HIGH。
2. **P1 高优先级**：入境/签证类 HIGH 风险（材料阻断缺失、政策变化），以及其余 HIGH 风险。
3. **P2 一般**：MEDIUM 风险（天气、汇率等可汇总项）。
4. **P3 信息性**：LOW 风险（进度与非紧急更新）。

``FLIGHT_STATUS_ALERT`` 等类型只有在接入真实航班源后才会出现；规则先就位，避免新信号接入时
又临时拍脑袋定级。
"""

from __future__ import annotations

PRIORITY_P0 = "P0"
PRIORITY_P1 = "P1"
PRIORITY_P2 = "P2"
PRIORITY_P3 = "P3"

PRIORITY_LABELS = {
    PRIORITY_P0: "紧急",
    PRIORITY_P1: "高优先级",
    PRIORITY_P2: "一般",
    PRIORITY_P3: "信息性",
}

#: 这些类型 + HIGH 风险 ⇒ 可能错过重要预约/交通（P0）。
_URGENT_TYPES = frozenset({"FLIGHT_STATUS_ALERT", "PLAN_UNPLACED_VISITS"})


def classify(*, suggestion_type: str, severity: str) -> str:
    """按建议类型与风险等级判定通知等级；未知组合退回保守的中档。"""
    severity = (severity or "MEDIUM").upper()
    kind = suggestion_type.upper()
    if severity == "HIGH":
        if kind in _URGENT_TYPES:
            return PRIORITY_P0
        return PRIORITY_P1
    if severity == "MEDIUM":
        return PRIORITY_P2
    if severity == "LOW":
        return PRIORITY_P3
    return PRIORITY_P2


def label(priority: str) -> str:
    return PRIORITY_LABELS.get(priority.upper(), priority)


__all__ = [
    "PRIORITY_LABELS",
    "PRIORITY_P0",
    "PRIORITY_P1",
    "PRIORITY_P2",
    "PRIORITY_P3",
    "classify",
    "label",
]
