"""契约测试：哈希内容寻址、规范化序列化、非法输入拒绝、规则顺序冻结。"""

from __future__ import annotations

import json

import pytest
from fixtures import TZ_HOME, at, four_city_problem, make_problem, make_stop, vid
from pydantic import ValidationError
from travel_planner.contracts import (
    HASH_PREFIX,
    VALIDATION_RULES,
    Constraints,
    PlanningProblem,
    canonical_problem_payload,
    compute_problem_hash,
)


def test_problem_hash_is_prefixed_and_stable() -> None:
    first = four_city_problem()
    second = four_city_problem()

    assert first.problem_hash() == second.problem_hash()
    assert first.problem_hash().startswith(HASH_PREFIX)
    assert len(first.problem_hash()) == len(HASH_PREFIX) + 64
    assert first.problem_hash() == compute_problem_hash(second)


def test_problem_hash_is_content_addressed() -> None:
    base = four_city_problem()
    changed_capacity = four_city_problem(max_stops_per_day=3)
    changed_value = four_city_problem()
    mutated_stops = tuple(
        stop.model_copy(update={"business_value": stop.business_value + 1.0})
        for stop in changed_value.stops
    )
    with_mutated_value = base.model_copy(update={"stops": mutated_stops})

    assert base.problem_hash() != changed_capacity.problem_hash()
    assert base.problem_hash() != with_mutated_value.problem_hash()


def test_problem_hash_includes_solver_budget() -> None:
    """搜索预算会改变求解搜索范围，因此必须进入哈希（第 6.4 节）。"""
    assert four_city_problem(budget_seconds=1).problem_hash() != four_city_problem(
        budget_seconds=3
    ).problem_hash()


def test_canonical_payload_matches_frozen_json_convention() -> None:
    """必须与 API 层 `canonical_json` 同一套约定：键排序 + 紧凑分隔符 + 不转义非 ASCII。"""
    problem = four_city_problem()
    canonical = canonical_problem_payload(problem)
    expected = json.dumps(
        problem.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    assert canonical == expected
    assert " " not in canonical.replace(TZ_HOME, "")
    assert canonical.startswith('{"constraints":')


def test_duplicate_visit_id_is_rejected() -> None:
    stop = make_stop(1, "berlin")
    with pytest.raises(ValidationError, match="duplicate visit_id"):
        make_problem((stop, stop))


def test_unknown_immovable_is_rejected() -> None:
    with pytest.raises(ValidationError, match="immovable_stop_ids not present"):
        make_problem((make_stop(1, "berlin"),), immovable=(vid(99),))


def test_unknown_locked_is_rejected() -> None:
    with pytest.raises(ValidationError, match="locked_order not present"):
        make_problem((make_stop(1, "berlin"),), locked=(vid(1), vid(99)))


def test_duplicate_locked_is_rejected() -> None:
    with pytest.raises(ValidationError, match="locked_order contains duplicate"):
        make_problem((make_stop(1, "berlin"), make_stop(2, "munich")), locked=(vid(1), vid(1)))


def test_contract_is_strict_and_frozen() -> None:
    problem = four_city_problem()

    with pytest.raises(ValidationError):
        problem.solver_budget_seconds = 5
    with pytest.raises(ValidationError):
        PlanningProblem(**{**problem.model_dump(), "unexpected_field": 1})
    with pytest.raises(ValidationError):
        Constraints(max_stops_per_day=0)


def test_problem_window_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="after start"):
        make_problem((make_stop(1, "berlin"),), start=at(10, 9), end=at(9, 9))


def test_validation_rule_order_is_frozen() -> None:
    assert VALIDATION_RULES == (
        "STOP_COVERAGE",
        "NO_DUPLICATE_STOP",
        "DAY_WITHIN_WINDOW",
        "TIME_WINDOW",
        "SERVICE_DURATION",
        "TRAVEL_TIME_CONSISTENCY",
        "MAX_STOPS_PER_DAY",
        "LOCKED_ORDER",
        "OBJECTIVE_RECOMPUTED",
    )


def test_stop_lookup_by_id() -> None:
    problem = four_city_problem()
    assert problem.stop_by_id(vid(3)).place_id == problem.stops[2].place_id
    with pytest.raises(KeyError):
        problem.stop_by_id(vid(404))