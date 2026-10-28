import Foundation

/// `kind:"ADDED|REMOVED|MOVED|RETIMED|UNCHANGED"`.
enum PlanChangeKind: String, Codable, CaseIterable, Identifiable {
    case added = "ADDED"
    case removed = "REMOVED"
    case moved = "MOVED"
    case retimed = "RETIMED"
    case unchanged = "UNCHANGED"
    case unknown = "UNKNOWN"

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = PlanChangeKind(rawValue: raw) ?? .unknown
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

    /// Whether this change is worth surfacing prominently in the diff screen.
    var isMaterial: Bool { self != .unchanged }
}

/// `base:{sequence, arrive, depart, date}|null` — where a visit sat in one of the two plans.
struct StopSnapshot: Codable, Equatable {
    var sequence: Int?
    var arrive: Date?
    var depart: Date?
    /// Calendar day, kept as a `String` for the same reason as `PlanDay.date`.
    var date: String?

    init(sequence: Int? = nil, arrive: Date? = nil, depart: Date? = nil, date: String? = nil) {
        self.sequence = sequence
        self.arrive = arrive
        self.depart = depart
        self.date = date
    }

    var sequenceLabel: String {
        guard let sequence else { return "—" }
        return "#\(sequence)"
    }

    var arriveLabel: String {
        guard let arrive else { return "—" }
        return ISO8601.timeDisplay(from: arrive)
    }

    var summary: String {
        "\(date ?? "—") · \(sequenceLabel) · \(arriveLabel)"
    }
}

/// `changes:[{kind, visit_id, place_name, base, compare}]`.
struct PlanChange: Codable, Equatable, Identifiable {
    var kind: PlanChangeKind
    var visitId: String
    var placeName: String?
    var base: StopSnapshot?
    var compare: StopSnapshot?

    init(
        kind: PlanChangeKind,
        visitId: String,
        placeName: String? = nil,
        base: StopSnapshot? = nil,
        compare: StopSnapshot? = nil
    ) {
        self.kind = kind
        self.visitId = visitId
        self.placeName = placeName
        self.base = base
        self.compare = compare
    }

    var id: String { "\(kind.rawValue)-\(visitId)" }

    var displayName: String { placeName ?? visitId }

    var sequenceDelta: Int? {
        guard let lhs = base?.sequence, let rhs = compare?.sequence else { return nil }
        return rhs - lhs
    }

    var travelDescription: String {
        switch kind {
        case .added:
            return "加入 \(compare?.summary ?? "—")"
        case .removed:
            return "移出原 \(base?.summary ?? "—")"
        case .moved:
            guard let delta = sequenceDelta else { return "顺序变化" }
            let direction = delta > 0 ? "后移" : "前移"
            return "位置 \(direction) \(abs(delta)) 位（\(base?.sequenceLabel ?? "—") → \(compare?.sequenceLabel ?? "—")）"
        case .retimed:
            return "\(base?.arriveLabel ?? "—") → \(compare?.arriveLabel ?? "—")（\(compare?.date ?? base?.date ?? "—")）"
        case .unchanged:
            return "无变化"
        case .unknown:
            return "服务端返回了未知变更类型"
        }
    }
}

/// `summary:{added, removed, moved, retimed, total_travel_minutes_delta, total_distance_km_delta}`.
struct PlanDiffSummary: Codable, Equatable {
    var added: Int?
    var removed: Int?
    var moved: Int?
    var retimed: Int?
    var totalTravelMinutesDelta: Int?
    var totalDistanceKmDelta: Double?

    init(
        added: Int? = nil,
        removed: Int? = nil,
        moved: Int? = nil,
        retimed: Int? = nil,
        totalTravelMinutesDelta: Int? = nil,
        totalDistanceKmDelta: Double? = nil
    ) {
        self.added = added
        self.removed = removed
        self.moved = moved
        self.retimed = retimed
        self.totalTravelMinutesDelta = totalTravelMinutesDelta
        self.totalDistanceKmDelta = totalDistanceKmDelta
    }

    var addedCount: Int { added ?? 0 }
    var removedCount: Int { removed ?? 0 }
    var movedCount: Int { moved ?? 0 }
    var retimedCount: Int { retimed ?? 0 }

    var totalChanges: Int { addedCount + removedCount + movedCount + retimedCount }

    var displayText: String {
        "新增 \(addedCount) · 移除 \(removedCount) · 换序 \(movedCount) · 改时 \(retimedCount)"
    }

    var travelDeltaLabel: String {
        let minutes = totalTravelMinutesDelta ?? 0
        let sign = minutes > 0 ? "+" : ""
        return "\(sign)\(minutes) min"
    }

    var distanceDeltaLabel: String {
        let km = totalDistanceKmDelta ?? 0
        let sign = km > 0 ? "+" : ""
        return String(format: "%@%.1f km", sign, km)
    }

    /// True when the new plan is strictly cheaper on travel time — the usual reason to re-plan.
    var shortensTravel: Bool {
        (totalTravelMinutesDelta ?? 0) < 0 || (totalDistanceKmDelta ?? 0) < 0
    }
}

/// `GET /v1/trips/{id}/plans/{plan_id}:diff?against={other_id}` response.
struct PlanDiff: Codable, Equatable {
    var basePlanId: String
    var comparePlanId: String
    var summary: PlanDiffSummary?
    var changes: [PlanChange]?

    init(
        basePlanId: String,
        comparePlanId: String,
        summary: PlanDiffSummary? = nil,
        changes: [PlanChange]? = nil
    ) {
        self.basePlanId = basePlanId
        self.comparePlanId = comparePlanId
        self.summary = summary
        self.changes = changes
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.basePlanId = try container.decode(String.self, forKey: .basePlanId)
        self.comparePlanId = try container.decode(String.self, forKey: .comparePlanId)
        self.summary = try container.decodeIfPresent(PlanDiffSummary.self, forKey: .summary)
        self.changes = try container.decodeIfPresent([PlanChange].self, forKey: .changes)
    }

    private enum CodingKeys: String, CodingKey {
        case basePlanId
        case comparePlanId
        case summary
        case changes
    }

    var changeList: [PlanChange] { changes ?? [] }

    var materialChanges: [PlanChange] { changeList.filter { $0.kind.isMaterial } }

    var unchangedCount: Int { changeList.filter { $0.kind == .unchanged }.count }

    var hasChanges: Bool { !materialChanges.isEmpty }

    var headline: String {
        let base = "v\(basePlanId)"
        let compare = "v\(comparePlanId)"
        if changeList.isEmpty { return "\(base) → \(compare)：无差异数据" }
        if !hasChanges { return "\(base) → \(compare)：两份计划完全一致" }
        return "\(base) → \(compare)：\(summary?.displayText ?? "\(materialChanges.count) 项变更")"
    }

    func changes(of kind: PlanChangeKind) -> [PlanChange] {
        changeList.filter { $0.kind == kind }
    }
}