import Foundation

/// 规划引擎契约（Swift 直译自 `packages/planning-engine/travel_planner/contracts.py`，ADR-0036）。
///
/// 与 UI 层 DTO（PlanDay/Trip 等）**有意分开**：引擎契约是纯算法输入输出，
/// UI DTO 是服务端响应形状。本地模式由 LocalPlanRepository 负责两者转换。
/// 语义红线：所有速度/固定开销常量内嵌（非配置），保证确定性。

enum EngineMovementMode: String, Codable, CaseIterable {
    case ground = "GROUND"
    case rail = "RAIL"
    case air = "AIR"
    case sea = "SEA"
}

enum EngineSolverKind: String, Codable {
    case heuristic
    case ortools
}

enum EngineUnplacedReason: String, Codable {
    case noFeasibleSlot = "NO_FEASIBLE_SLOT"
    case timeWindowInfeasible = "TIME_WINDOW_INFEASIBLE"
    case capacityExceeded = "CAPACITY_EXCEEDED"
}

/// 时间窗（瞬时时间，已含时区）。
struct EngineTimeWindow: Codable, Equatable {
    var start: Date
    var end: Date
}

/// 出行窗口。
struct EngineProblemWindow: Codable, Equatable {
    var start: Date
    var end: Date
}

struct EngineStopInput: Codable, Equatable {
    var visitId: UUID
    var placeId: UUID
    var lat: Double
    var lon: Double
    var serviceMinutes: Int
    var timeWindows: [EngineTimeWindow]
    var priority: Int
    var businessValue: Double
    var movementMode: EngineMovementMode
}

struct EngineObjectiveWeights: Codable, Equatable {
    var travel: Double = 1.0
    var value: Double = 0.001
}

struct EngineConstraints: Codable, Equatable {
    var maxStopsPerDay: Int
    var immovableStopIds: [UUID]
    var lockedOrder: [UUID]

    init(maxStopsPerDay: Int = 4, immovableStopIds: [UUID] = [], lockedOrder: [UUID] = []) {
        self.maxStopsPerDay = maxStopsPerDay
        self.immovableStopIds = immovableStopIds
        self.lockedOrder = lockedOrder
    }
}

struct EngineOrigin: Codable, Equatable {
    var placeId: UUID
    var lat: Double
    var lon: Double
}

/// 规划问题（规范化输入：可哈希、可重放）。
struct EnginePlanningProblem: Codable, Equatable {
    var tripId: UUID
    var timezoneHome: String
    var origin: EngineOrigin
    var window: EngineProblemWindow
    var stops: [EngineStopInput]
    var objectiveWeights: EngineObjectiveWeights
    var constraints: EngineConstraints
    var travelEstimates: [EngineTravelEstimate]
    var solverBudgetSeconds: Int

    init(
        tripId: UUID,
        timezoneHome: String,
        origin: EngineOrigin,
        window: EngineProblemWindow,
        stops: [EngineStopInput],
        objectiveWeights: EngineObjectiveWeights = EngineObjectiveWeights(),
        constraints: EngineConstraints = EngineConstraints(),
        travelEstimates: [EngineTravelEstimate] = [],
        solverBudgetSeconds: Int = 10
    ) {
        self.tripId = tripId
        self.timezoneHome = timezoneHome
        self.origin = origin
        self.window = window
        self.stops = stops
        self.objectiveWeights = objectiveWeights
        self.constraints = constraints
        self.travelEstimates = travelEstimates
        self.solverBudgetSeconds = solverBudgetSeconds
    }

    func stop(byId id: UUID) -> EngineStopInput? {
        stops.first { $0.visitId == id }
    }
}

/// Provider 提供的逐段估算（本地版由 OSRM/离线估算生成）。
struct EngineTravelEstimate: Codable, Equatable {
    var fromVisitId: UUID?
    var toVisitId: UUID
    var mode: EngineMovementMode
    var travelMinutes: Int
    var distanceKm: Double
    var costAmount: Double?
    var costCurrency: String?
    var source: String
    var stale: Bool
}

// MARK: - 输出契约

struct EngineStopPlan: Codable, Equatable {
    var visitId: UUID
    var sequence: Int
    var arrive: Date
    var depart: Date
    var travelMinutes: Int
}

struct EngineDayPlan: Codable, Equatable {
    var date: Date
    var stops: [EngineStopPlan]
}

struct EngineTravelLegPlan: Codable, Equatable {
    var fromVisitId: UUID?
    var toVisitId: UUID
    var dayIndex: Int
    var mode: EngineMovementMode
    var travelMinutes: Int
    var distanceKm: Double
    var costAmount: Double?
    var costCurrency: String?
    var source: String
    var stale: Bool
}

struct EngineUnplacedStop: Codable, Equatable {
    var visitId: UUID
    var reason: EngineUnplacedReason
}

enum EngineCheckStatus: String, Codable {
    case pass_ = "PASS"
    case fail = "FAIL"
}

struct EngineValidationCheck: Codable, Equatable {
    var rule: String
    var status: EngineCheckStatus
    var detail: String?
}

struct EngineValidationReport: Codable, Equatable {
    var feasible: Bool
    var checks: [EngineValidationCheck]

    var failures: [EngineValidationCheck] {
        checks.filter { $0.status == .fail }
    }
}

enum EngineSolutionStatus: String, Codable {
    case optimal = "OPTIMAL"
    case feasible = "FEASIBLE"
    case partial = "PARTIAL"
    case infeasible = "INFEASIBLE"
    case unknown = "UNKNOWN"
    case error = "ERROR"
}

/// 求解器内部结果（`SolveOutcome`）。
struct EngineSolveOutcome: Codable, Equatable {
    var solver: EngineSolverKind
    var days: [EngineDayPlan]
    var legs: [EngineTravelLegPlan]
    var unplaced: [EngineUnplacedStop]
    var travelMinutesTotal: Int
    var valuePlaced: Double
    var objectiveValue: Double
    var degraded: Bool
}

/// 独立验证器的 9 条规则（名称冻结）。
enum EngineRule {
    static let stopCoverage = "STOP_COVERAGE"
    static let noDuplicateStop = "NO_DUPLICATE_STOP"
    static let dayWithinWindow = "DAY_WITHIN_WINDOW"
    static let timeWindow = "TIME_WINDOW"
    static let serviceDuration = "SERVICE_DURATION"
    static let travelTimeConsistency = "TRAVEL_TIME_CONSISTENCY"
    static let maxStopsPerDay = "MAX_STOPS_PER_DAY"
    static let lockedOrder = "LOCKED_ORDER"
    static let objectiveRecomputed = "OBJECTIVE_RECOMPUTED"

    static let all: [String] = [
        stopCoverage, noDuplicateStop, dayWithinWindow, timeWindow, serviceDuration,
        travelTimeConsistency, maxStopsPerDay, lockedOrder, objectiveRecomputed,
    ]
}
