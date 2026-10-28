"""全球商务出行规划引擎（`travel_planner`）。

**纯计算包**：无 IO、不碰数据库、不与用户交互、不做任何外部预订。
上层（`services/api`）负责把领域数据规范化为 `PlanningProblem`，
再把本包产出的、**已通过独立验证**的 `PlanningResult` 持久化为不可变计划版本。

对外主入口：

    from travel_planner import plan_trip

    result = plan_trip(problem)          # 自动选择求解器
    assert result.validation.feasible    # 可行性只能由验证器宣布
"""

from __future__ import annotations

from travel_planner.contracts import (
    HASH_PREFIX,
    STRICT,
    VALIDATION_RULES,
    CheckStatus,
    Constraints,
    DayPlan,
    MovementMode,
    Objective,
    ObjectiveWeights,
    Origin,
    PlanningProblem,
    PlanningResult,
    PrimaryObjective,
    ProblemWindow,
    SolutionStatus,
    SolverKind,
    StopInput,
    StopPlan,
    TimeWindow,
    TravelEstimate,
    TravelLegPlan,
    UnplacedReason,
    UnplacedStop,
    ValidationCheck,
    ValidationReport,
    canonical_problem_payload,
    compute_problem_hash,
)
from travel_planner.feasibility import FeasibilityOutcome, check_feasibility, rank_key
from travel_planner.geometry import haversine_km, travel_minutes
from travel_planner.outcome import SolveOutcome, SolverFailed, SolverUnavailable
from travel_planner.pipeline import PIPELINE_STAGES, plan_trip, preferred_solver, resolve_solver
from travel_planner.solvers.ortools_solver import is_available
from travel_planner.validator import validate

__version__ = "1.0.0"

__all__ = [
    "HASH_PREFIX",
    "PIPELINE_STAGES",
    "STRICT",
    "VALIDATION_RULES",
    "CheckStatus",
    "Constraints",
    "DayPlan",
    "FeasibilityOutcome",
    "MovementMode",
    "Objective",
    "ObjectiveWeights",
    "Origin",
    "PlanningProblem",
    "PlanningResult",
    "PrimaryObjective",
    "ProblemWindow",
    "SolveOutcome",
    "SolverFailed",
    "SolutionStatus",
    "SolverKind",
    "SolverUnavailable",
    "StopInput",
    "StopPlan",
    "TimeWindow",
    "TravelEstimate",
    "TravelLegPlan",
    "UnplacedReason",
    "UnplacedStop",
    "ValidationCheck",
    "ValidationReport",
    "__version__",
    "canonical_problem_payload",
    "check_feasibility",
    "compute_problem_hash",
    "haversine_km",
    "is_available",
    "plan_trip",
    "preferred_solver",
    "rank_key",
    "resolve_solver",
    "travel_minutes",
    "validate",
]