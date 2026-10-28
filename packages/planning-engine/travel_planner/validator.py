"""独立验证器：不复用求解器逻辑，按规则名逐条独立复算 `PlanningResult`。

设计约束（第 6.1 节不变量 I1）：
- 本模块**不导入** `travel_planner.scheduling`（求解器的时间构造器），
  日界与行程时间判据在此独立实现，避免"自己校验自己"。
- 只依赖 `geometry`（行程时间的**定义**）与契约常量。
- 任何一条规则 FAIL ⇒ `feasible = False`；因此"不可行"永远不可能被伪装成"可行"。
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from travel_planner.contracts import (
    RULE_DAY_WITHIN_WINDOW,
    RULE_LOCKED_ORDER,
    RULE_MAX_STOPS_PER_DAY,
    RULE_NO_DUPLICATE_STOP,
    RULE_OBJECTIVE_RECOMPUTED,
    RULE_SERVICE_DURATION,
    RULE_STOP_COVERAGE,
    RULE_TIME_WINDOW,
    RULE_TRAVEL_TIME_CONSISTENCY,
    CheckStatus,
    DayPlan,
    PlanningProblem,
    UnplacedStop,
    ValidationCheck,
    ValidationReport,
)
from travel_planner.geometry import travel_minutes

_ONE_SECOND = timedelta(seconds=1)
_LOCKED_ORDER_TOLERANCE = timedelta(seconds=0)


def _day_bounds(problem: PlanningProblem) -> dict[date_type, tuple[datetime, datetime]]:
    """独立复算逐日可用区间（与调度器实现无关的第二份实现）。"""
    tz = ZoneInfo(problem.timezone_home)
    start = problem.window.start
    end = problem.window.end
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    if end.tzinfo is None:
        end = end.replace(tzinfo=tz)

    bounds: dict[date_type, tuple[datetime, datetime]] = {}
    cursor = start.astimezone(tz).date()
    last = end.astimezone(tz).date()
    while cursor <= last:
        midnight = datetime.combine(cursor, time(0, 0, 0), tzinfo=tz)
        day_open = start if start > midnight else midnight
        day_close = midnight + timedelta(days=1) - _ONE_SECOND
        if end < day_close:
            day_close = end
        bounds[cursor] = (day_open, day_close)
        cursor = cursor + timedelta(days=1)
    return bounds


def _check(rule: str, ok: bool, detail: str | None = None) -> ValidationCheck:
    return ValidationCheck(
        rule=rule,
        status=CheckStatus.PASS if ok else CheckStatus.FAIL,
        detail=detail,
    )


def validate(
    *,
    problem: PlanningProblem,
    days: tuple[DayPlan, ...],
    unplaced: tuple[UnplacedStop, ...],
    objective_value: float,
) -> ValidationReport:
    """按冻结顺序输出 9 条规则的裁决。"""
    checks: list[ValidationCheck] = []

    by_id = {stop.visit_id: stop for stop in problem.stops}
    all_ids = set(by_id)
    placed_ids: list[UUID] = [stop.visit_id for day in days for stop in day.stops]
    unplaced_ids: list[UUID] = [item.visit_id for item in unplaced]
    immovable = set(problem.constraints.immovable_stop_ids)

    # 1. STOP_COVERAGE —— 每个停靠恰好出现一次，且不可动停靠必须被排入
    placed_set = set(placed_ids)
    unplaced_set = set(unplaced_ids)
    missing = sorted(str(v) for v in all_ids - placed_set - unplaced_set)
    overlap = sorted(str(v) for v in placed_set & unplaced_set)
    immovable_missing = sorted(str(v) for v in immovable - placed_set)
    unknown_placed = sorted(str(v) for v in placed_set - all_ids)
    coverage_ok = not (missing or overlap or immovable_missing or unknown_placed)
    checks.append(
        _check(
            RULE_STOP_COVERAGE,
            coverage_ok,
            None
            if coverage_ok
            else (
                f"missing={missing} overlap={overlap} "
                f"immovable_unplaced={immovable_missing} unknown={unknown_placed}"
            ),
        )
    )

    # 2. NO_DUPLICATE_STOP
    duplicates = sorted({str(v) for v in placed_ids if placed_ids.count(v) > 1})
    checks.append(
        _check(
            RULE_NO_DUPLICATE_STOP,
            not duplicates,
            None if not duplicates else f"duplicated={duplicates}",
        )
    )

    # 3. DAY_WITHIN_WINDOW —— 日期合法，且到/离落在该日区间内
    bounds = _day_bounds(problem)
    day_issues: list[str] = []
    seen_dates: set[date_type] = set()
    for day in days:
        if day.date in seen_dates:
            day_issues.append(f"{day.date}:duplicate-day")
        seen_dates.add(day.date)
        window = bounds.get(day.date)
        if window is None:
            day_issues.append(f"{day.date}:outside-trip-window")
            continue
        day_open, day_close = window
        for stop in day.stops:
            if not (day_open <= stop.arrive <= day_close):
                day_issues.append(f"{day.date}:{stop.visit_id}:arrive-outside-day")
            if not (day_open <= stop.depart <= day_close):
                day_issues.append(f"{day.date}:{stop.visit_id}:depart-outside-day")
            if stop.depart < stop.arrive:
                day_issues.append(f"{day.date}:{stop.visit_id}:depart-before-arrive")
    checks.append(
        _check(
            RULE_DAY_WITHIN_WINDOW,
            not day_issues,
            None if not day_issues else ";".join(day_issues[:10]),
        )
    )

    # 4. TIME_WINDOW —— 到达时刻须落在该停靠的某个时间窗内
    window_issues: list[str] = []
    for day in days:
        for stop in day.stops:
            source = by_id.get(stop.visit_id)
            if source is None or not source.time_windows:
                continue
            if not any(w.start <= stop.arrive <= w.end for w in source.time_windows):
                window_issues.append(f"{stop.visit_id}:arrive={stop.arrive.isoformat()}")
    checks.append(
        _check(
            RULE_TIME_WINDOW,
            not window_issues,
            None if not window_issues else ";".join(window_issues[:10]),
        )
    )

    # 5. SERVICE_DURATION —— 停留时长精确等于输入的服务时长
    service_issues: list[str] = []
    for day in days:
        for stop in day.stops:
            source = by_id.get(stop.visit_id)
            if source is None:
                continue
            actual = int((stop.depart - stop.arrive).total_seconds() // 60)
            if actual != source.service_minutes:
                service_issues.append(f"{stop.visit_id}:expected={source.service_minutes} actual={actual}")
    checks.append(
        _check(
            RULE_SERVICE_DURATION,
            not service_issues,
            None if not service_issues else ";".join(service_issues[:10]),
        )
    )

    # 6. TRAVEL_TIME_CONSISTENCY —— 行程时间与几何模型一致（整条路径连续链路）
    #
    # 多天连续性语义（与调度器契约一致，此处独立复算）：
    # 第 1 天自 `problem.origin` 出发；第 k 天自第 k-1 天最后一个已排停靠出发。
    # 因此这里按日期升序迭代**整条**路径，`prev` 变量跨天保持；空天不重置位置
    # （空天没有任何停靠，自然不触碰 `prev`，于是自动回溯到最近有停靠的一天）。
    travel_issues: list[str] = []
    estimates = {
        (item.from_visit_id, item.to_visit_id, item.mode): item
        for item in problem.travel_estimates
    }
    prev_lat, prev_lon = problem.origin.lat, problem.origin.lon
    prev_visit_id = None
    for day in sorted(days, key=lambda item: item.date):
        for stop in day.stops:
            source = by_id.get(stop.visit_id)
            if source is None:
                travel_issues.append(f"{stop.visit_id}:unknown-stop")
                continue
            estimate = estimates.get((prev_visit_id, source.visit_id, source.movement_mode))
            expected = (
                estimate.travel_minutes
                if estimate is not None
                else travel_minutes(
                    prev_lat, prev_lon, source.lat, source.lon, source.movement_mode
                )
            )
            if stop.travel_minutes != expected:
                travel_issues.append(
                    f"{stop.visit_id}:expected={expected} actual={stop.travel_minutes}"
                )
            prev_lat, prev_lon = source.lat, source.lon
            prev_visit_id = source.visit_id
    checks.append(
        _check(
            RULE_TRAVEL_TIME_CONSISTENCY,
            not travel_issues,
            None if not travel_issues else ";".join(travel_issues[:10]),
        )
    )

    # 7. MAX_STOPS_PER_DAY
    limit = problem.constraints.max_stops_per_day
    over_limit = [f"{day.date}:{len(day.stops)}>{limit}" for day in days if len(day.stops) > limit]
    checks.append(
        _check(
            RULE_MAX_STOPS_PER_DAY,
            not over_limit,
            None if not over_limit else ";".join(over_limit),
        )
    )

    # 8. LOCKED_ORDER —— 锁定顺序按到达时刻严格递增，且前置未排时后继不得单独排入
    arrival_by_id: dict[UUID, datetime] = {
        stop.visit_id: stop.arrive for day in days for stop in day.stops
    }
    order_issues: list[str] = []
    locked = problem.constraints.locked_order
    for first, second in zip(locked, locked[1:], strict=False):
        first_arrive = arrival_by_id.get(first)
        second_arrive = arrival_by_id.get(second)
        if second_arrive is not None and first_arrive is None:
            order_issues.append(f"{second}:placed-before-{first}")
            continue
        if first_arrive is not None and second_arrive is not None:
            if second_arrive < first_arrive - _LOCKED_ORDER_TOLERANCE:
                order_issues.append(f"{first}->{second}:order-violated")
    checks.append(
        _check(
            RULE_LOCKED_ORDER,
            not order_issues,
            None if not order_issues else ";".join(order_issues[:10]),
        )
    )

    # 9. OBJECTIVE_RECOMPUTED —— 独立按定义复算目标值
    weights = problem.objective.weights
    recomputed_travel = sum(stop.travel_minutes for day in days for stop in day.stops)
    recomputed_value = sum(
        by_id[stop.visit_id].business_value for day in days for stop in day.stops if stop.visit_id in by_id
    )
    recomputed = round(recomputed_travel * weights.travel - recomputed_value * weights.value, 6)
    objective_ok = abs(recomputed - objective_value) <= 1e-6
    checks.append(
        _check(
            RULE_OBJECTIVE_RECOMPUTED,
            objective_ok,
            None if objective_ok else f"expected={recomputed} reported={objective_value}",
        )
    )

    return ValidationReport(
        feasible=all(item.status is CheckStatus.PASS for item in checks),
        checks=tuple(checks),
    )