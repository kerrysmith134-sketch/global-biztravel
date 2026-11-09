import Foundation

/// 停靠在两版计划之间的变化类型（后端 `PlanStopDeltaRead.change`）。
///
/// 走 `convertFromSnakeCase` 的全局解码器：wire 上是枚举字符串，未知值一律映射为
/// `.unknown`——客户端不能因为服务端新增变更类型而整体解码失败。
enum PlanStopChange: String, Codable, CaseIterable, Identifiable {
    case added = "ADDED"
    case removed = "REMOVED"
    case moved = "MOVED"
    case retimed = "RETIMED"
    case unchanged = "UNCHANGED"
    case unknown = "UNKNOWN"

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = PlanStopChange(rawValue: raw) ?? .unknown
    }

    var id: String { rawValue }

    var label: String {
        switch self {
        case .added: return "新增"
        case .removed: return "移除"
        case .moved: return "换序"
        case .retimed: return "改时"
        case .unchanged: return "未变"
        case .unknown: return "未知"
        }
    }

    var glyph: String {
        switch self {
        case .added: return "plus.circle.fill"
        case .removed: return "minus.circle.fill"
        case .moved: return "arrow.up.arrow.down.circle.fill"
        case .retimed: return "clock.arrow.circlepath"
        case .unchanged: return "equal.circle"
        case .unknown: return "questionmark.circle"
        }
    }

    /// 是否值得在比较页突出展示。
    var isMaterial: Bool { self != .unchanged }
}

/// 单个停靠在两版计划里的对照（后端 `PlanStopDeltaRead`）。
///
/// 字段是扁平的 `*_from` / `*_to`，不是嵌套快照——这是后端契约的形状。
struct PlanStopDelta: Codable, Equatable, Identifiable {
    var visitId: String
    var change: PlanStopChange
    var dayIndexFrom: Int?
    var dayIndexTo: Int?
    var sequenceFrom: Int?
    var sequenceTo: Int?
    var arriveAtFrom: Date?
    var arriveAtTo: Date?
    var departAtFrom: Date?
    var departAtTo: Date?
    var travelMinutesFrom: Int?
    var travelMinutesTo: Int?
    var serviceMinutesFrom: Int?
    var serviceMinutesTo: Int?

    var id: String { "\(change.rawValue)-\(visitId)" }

    var displayName: String { visitId }

    var sequenceDelta: Int? {
        guard let from = sequenceFrom, let to = sequenceTo else { return nil }
        return to - from
    }

    var travelDescription: String {
        switch change {
        case .added:
            return "加入第 \(dayIndexTo.map { $0 + 1 } ?? 0) 天"
        case .removed:
            return "移出第 \(dayIndexFrom.map { $0 + 1 } ?? 0) 天"
        case .moved:
            guard let delta = sequenceDelta else { return "顺序变化" }
            return "位置\(delta > 0 ? "后移" : "前移") \(abs(delta)) 位"
        case .retimed:
            let from = arriveAtFrom.map { ISO8601.timeDisplay(from: $0) } ?? "—"
            let to = arriveAtTo.map { ISO8601.timeDisplay(from: $0) } ?? "—"
            return "\(from) → \(to)"
        case .unchanged:
            return "无变化"
        case .unknown:
            return "服务端返回了未知变更类型"
        }
    }
}

/// 两版计划的变化汇总（后端 `PlanVersionDiffRead.summary`）。
struct PlanDiffSummary: Codable, Equatable {
    var added: Int?
    var removed: Int?
    var moved: Int?
    var retimed: Int?
    var unchanged: Int?
    var total: Int?

    var addedCount: Int { added ?? 0 }
    var removedCount: Int { removed ?? 0 }
    var movedCount: Int { moved ?? 0 }
    var retimedCount: Int { retimed ?? 0 }

    var totalChanges: Int { addedCount + removedCount + movedCount + retimedCount }

    var displayText: String {
        "新增 \(addedCount) · 移除 \(removedCount) · 换序 \(movedCount) · 改时 \(retimedCount)"
    }
}

/// 未排项在两版之间的变化（后端 `PlanUnplacedDeltaRead`）：
/// `action` 为 `ADDED`（新版新增未排）或 `REMOVED`（新版已能排下）。
struct PlanUnplacedDelta: Codable, Equatable, Identifiable {
    var visitId: String
    var action: String
    var reason: String?
    var source: String?

    var id: String { "\(action)-\(visitId)" }
}

/// `GET /v1/trips/{id}/plans/{plan_id}:diff?against={other}` 响应（后端 `PlanVersionDiffRead`）。
///
/// 注意：字段形状以契约为准——`base_plan_version_id` / `target_plan_version_id` /
/// `stops` / 扁平的 `objective_*`、`travel_minutes_*`。此前 iOS 侧按“base/compare +
/// changes 快照”的旧假设实现，与真实响应完全不符，从未被解码路径覆盖到；加入契约样本
/// 与版本比较页后才暴露（见 issue：PlanDiff 模型漂移）。
struct PlanDiff: Codable, Equatable {
    var tripId: String?
    var basePlanVersionId: String
    var targetPlanVersionId: String
    var baseVersionNumber: Int?
    var targetVersionNumber: Int?
    var baseStatus: String?
    var targetStatus: String?
    var baseSolver: String?
    var targetSolver: String?
    var baseDegraded: Bool?
    var targetDegraded: Bool?
    var objectiveFrom: Double?
    var objectiveTo: Double?
    var objectiveDelta: Double?
    var travelMinutesFrom: Int?
    var travelMinutesTo: Int?
    var travelMinutesDelta: Int?
    var stops: [PlanStopDelta]?
    var unplaced: [PlanUnplacedDelta]?
    var summary: PlanDiffSummary?

    var stopList: [PlanStopDelta] { stops ?? [] }

    var materialStops: [PlanStopDelta] { stopList.filter { $0.change.isMaterial } }

    var unchangedCount: Int { stopList.filter { $0.change == .unchanged }.count }

    var hasChanges: Bool { !materialStops.isEmpty }

    /// 行驶时间变化的展示文本（顶层 `travel_minutes_delta`）。
    var travelDeltaLabel: String {
        let minutes = travelMinutesDelta ?? 0
        return "\(minutes > 0 ? "+" : "")\(minutes) min"
    }
}
