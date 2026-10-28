import Foundation

enum PlanStatus: String, Codable, CaseIterable, Identifiable {
    case draft = "DRAFT"
    case active = "ACTIVE"
    case superseded = "SUPERSEDED"

    var id: String { rawValue }

    var label: String {
        switch self {
        case .draft: return "草稿"
        case .active: return "已激活"
        case .superseded: return "已被取代"
        }
    }
}

/// List item from `GET /v1/trips/{id}/plans`.
struct PlanVersion: Codable, Equatable, Identifiable {
    var id: String
    var versionNumber: Int?
    var status: PlanStatus?
    var solver: String?
    var feasible: Bool?
    var degraded: Bool?
    var objectiveValue: Double?
    var solutionStatus: String?
    var costTotal: Double?
    var costCurrency: String?
    var costUnknownCount: Int?
    var stopCount: Int?
    var dayCount: Int?
    var createdAt: Date?

    init(
        id: String,
        versionNumber: Int? = nil,
        status: PlanStatus? = nil,
        solver: String? = nil,
        feasible: Bool? = nil,
        degraded: Bool? = nil,
        objectiveValue: Double? = nil,
        solutionStatus: String? = nil,
        costTotal: Double? = nil,
        costCurrency: String? = nil,
        costUnknownCount: Int? = nil,
        stopCount: Int? = nil,
        dayCount: Int? = nil,
        createdAt: Date? = nil
    ) {
        self.id = id
        self.versionNumber = versionNumber
        self.status = status
        self.solver = solver
        self.feasible = feasible
        self.degraded = degraded
        self.objectiveValue = objectiveValue
        self.solutionStatus = solutionStatus
        self.costTotal = costTotal
        self.costCurrency = costCurrency
        self.costUnknownCount = costUnknownCount
        self.stopCount = stopCount
        self.dayCount = dayCount
        self.createdAt = createdAt
    }

    private enum CodingKeys: String, CodingKey {
        case id
        case versionNumber
        case status
        case solver
        case feasible
        case degraded
        case objectiveValue
        case solutionStatus
        case costTotal
        case costCurrency
        case costUnknownCount
        case stopCount
        case dayCount
        case createdAt
        case validation
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.id = try container.decode(String.self, forKey: .id)
        self.versionNumber = try container.decodeIfPresent(Int.self, forKey: .versionNumber)
        self.status = try container.decodeIfPresent(PlanStatus.self, forKey: .status)
        self.solver = try container.decodeIfPresent(String.self, forKey: .solver)
        self.degraded = try container.decodeIfPresent(Bool.self, forKey: .degraded)
        self.objectiveValue = try container.decodeIfPresent(Double.self, forKey: .objectiveValue)
        self.solutionStatus = try container.decodeIfPresent(String.self, forKey: .solutionStatus)
        self.costTotal = try container.decodeIfPresent(Double.self, forKey: .costTotal)
        self.costCurrency = try container.decodeIfPresent(String.self, forKey: .costCurrency)
        self.costUnknownCount = try container.decodeIfPresent(Int.self, forKey: .costUnknownCount)
        self.stopCount = try container.decodeIfPresent(Int.self, forKey: .stopCount)
        self.dayCount = try container.decodeIfPresent(Int.self, forKey: .dayCount)
        self.createdAt = try container.decodeIfPresent(Date.self, forKey: .createdAt)
        let validation = try container.decodeIfPresent(ValidationReport.self, forKey: .validation)
        self.feasible = try container.decodeIfPresent(Bool.self, forKey: .feasible)
            ?? validation?.feasible
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encodeIfPresent(versionNumber, forKey: .versionNumber)
        try container.encodeIfPresent(status, forKey: .status)
        try container.encodeIfPresent(solver, forKey: .solver)
        try container.encodeIfPresent(feasible, forKey: .feasible)
        try container.encodeIfPresent(degraded, forKey: .degraded)
        try container.encodeIfPresent(objectiveValue, forKey: .objectiveValue)
        try container.encodeIfPresent(solutionStatus, forKey: .solutionStatus)
        try container.encodeIfPresent(costTotal, forKey: .costTotal)
        try container.encodeIfPresent(costCurrency, forKey: .costCurrency)
        try container.encodeIfPresent(costUnknownCount, forKey: .costUnknownCount)
        try container.encodeIfPresent(stopCount, forKey: .stopCount)
        try container.encodeIfPresent(dayCount, forKey: .dayCount)
        try container.encodeIfPresent(createdAt, forKey: .createdAt)
    }

    var versionLabel: String { "v\(versionNumber ?? 0)" }

    var feasibilityLabel: String {
        guard let feasible else { return "未知" }
        if !feasible { return "不可行" }
        return (degraded ?? false) ? "可行（降级求解）" : "可行"
    }

    var stopCountLabel: String { "\(stopCount ?? 0) 停靠 / \(dayCount ?? 0) 天" }

    var createdAtLabel: String {
        guard let createdAt else { return "—" }
        return ISO8601.shortDisplay(from: createdAt)
    }
}

/// Full plan from `GET /v1/trips/{id}/plans/{plan_id}`.
struct PlanVersionDetail: Codable, Equatable, Identifiable {
    var id: String
    var tripId: String?
    var versionNumber: Int?
    var status: PlanStatus?
    var solver: String?
    var feasible: Bool?
    var degraded: Bool?
    var objectiveValue: Double?
    var problemHash: String?
    var createdAt: Date?
    var validation: ValidationReport?
    var unplaced: [UnplacedVisit]?
    var days: [PlanDay]?
    var legs: [PlanTravelLeg]?
    var solutionStatus: String?
    var costTotal: Double?
    var costCurrency: String?
    var costUnknownCount: Int?

    init(
        id: String,
        tripId: String? = nil,
        versionNumber: Int? = nil,
        status: PlanStatus? = nil,
        solver: String? = nil,
        feasible: Bool? = nil,
        degraded: Bool? = nil,
        objectiveValue: Double? = nil,
        problemHash: String? = nil,
        createdAt: Date? = nil,
        validation: ValidationReport? = nil,
        unplaced: [UnplacedVisit]? = nil,
        days: [PlanDay]? = nil,
        legs: [PlanTravelLeg]? = nil,
        solutionStatus: String? = nil,
        costTotal: Double? = nil,
        costCurrency: String? = nil,
        costUnknownCount: Int? = nil
    ) {
        self.id = id
        self.tripId = tripId
        self.versionNumber = versionNumber
        self.status = status
        self.solver = solver
        self.feasible = feasible
        self.degraded = degraded
        self.objectiveValue = objectiveValue
        self.problemHash = problemHash
        self.createdAt = createdAt
        self.validation = validation
        self.unplaced = unplaced
        self.days = days
        self.legs = legs
        self.solutionStatus = solutionStatus
        self.costTotal = costTotal
        self.costCurrency = costCurrency
        self.costUnknownCount = costUnknownCount
    }

    private enum CodingKeys: String, CodingKey {
        case id
        case tripId
        case versionNumber
        case status
        case solver
        case feasible
        case degraded
        case objectiveValue
        case problemHash
        case createdAt
        case validation
        case unplaced
        case days
        case stops
        case legs
        case solutionStatus
        case costTotal
        case costCurrency
        case costUnknownCount
    }

    /// The backend returns `stops` as a flat, day-indexed list; the domain wants `days` for the
    /// itinerary UI. Decoding groups here so views never re-implement the grouping.
    private struct BackendStop: Decodable {
        var visitId: String
        var dayIndex: Int
        var dayDate: String
        var sequence: Int
        var arriveAt: Date?
        var departAt: Date?
        var travelMinutes: Int?
        var serviceMinutes: Int?
        var distanceKm: Double?
        var costAmount: Double?
        var costCurrency: String?
        var travelSource: String?
        var travelStale: Bool?
        var placeId: String?
        var placeName: String?
        var city: String?
        var countryCode: String?
        var latitude: Double?
        var longitude: Double?
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.id = try container.decode(String.self, forKey: .id)
        self.tripId = try container.decodeIfPresent(String.self, forKey: .tripId)
        self.versionNumber = try container.decodeIfPresent(Int.self, forKey: .versionNumber)
        self.status = try container.decodeIfPresent(PlanStatus.self, forKey: .status)
        self.solver = try container.decodeIfPresent(String.self, forKey: .solver)
        self.degraded = try container.decodeIfPresent(Bool.self, forKey: .degraded)
        self.objectiveValue = try container.decodeIfPresent(Double.self, forKey: .objectiveValue)
        self.problemHash = try container.decodeIfPresent(String.self, forKey: .problemHash)
        self.createdAt = try container.decodeIfPresent(Date.self, forKey: .createdAt)
        self.validation = try container.decodeIfPresent(ValidationReport.self, forKey: .validation)
        self.unplaced = try container.decodeIfPresent([UnplacedVisit].self, forKey: .unplaced)
        self.feasible = try container.decodeIfPresent(Bool.self, forKey: .feasible)
            ?? self.validation?.feasible
        self.solutionStatus = try container.decodeIfPresent(String.self, forKey: .solutionStatus)
        self.costTotal = try container.decodeIfPresent(Double.self, forKey: .costTotal)
        self.costCurrency = try container.decodeIfPresent(String.self, forKey: .costCurrency)
        self.costUnknownCount = try container.decodeIfPresent(Int.self, forKey: .costUnknownCount)
        self.legs = try container.decodeIfPresent([PlanTravelLeg].self, forKey: .legs)

        if let decodedDays = try container.decodeIfPresent([PlanDay].self, forKey: .days) {
            self.days = decodedDays
        } else {
            let flat = try container.decodeIfPresent([BackendStop].self, forKey: .stops) ?? []
            let grouped = Dictionary(grouping: flat, by: \.dayIndex)
            self.days = grouped.keys.sorted().compactMap { index in
                guard let items = grouped[index], let first = items.first else { return nil }
                let ordered = items.sorted { $0.sequence < $1.sequence }
                return PlanDay(
                    date: first.dayDate,
                    stops: ordered.map { stop in
                        PlanStop(
                            visitId: stop.visitId,
                            sequence: stop.sequence,
                            placeId: stop.placeId,
                            placeName: stop.placeName,
                            city: stop.city,
                            countryCode: stop.countryCode,
                            latitude: stop.latitude,
                            longitude: stop.longitude,
                            arrive: stop.arriveAt,
                            depart: stop.departAt,
                            travelMinutes: stop.travelMinutes,
                            distanceKm: stop.distanceKm,
                            serviceMinutes: stop.serviceMinutes,
                            costAmount: stop.costAmount,
                            costCurrency: stop.costCurrency,
                            travelSource: stop.travelSource,
                            stale: stop.travelStale
                        )
                    }
                )
            }
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encodeIfPresent(tripId, forKey: .tripId)
        try container.encodeIfPresent(versionNumber, forKey: .versionNumber)
        try container.encodeIfPresent(status, forKey: .status)
        try container.encodeIfPresent(solver, forKey: .solver)
        try container.encodeIfPresent(feasible, forKey: .feasible)
        try container.encodeIfPresent(degraded, forKey: .degraded)
        try container.encodeIfPresent(objectiveValue, forKey: .objectiveValue)
        try container.encodeIfPresent(problemHash, forKey: .problemHash)
        try container.encodeIfPresent(createdAt, forKey: .createdAt)
        try container.encodeIfPresent(validation, forKey: .validation)
        try container.encodeIfPresent(unplaced, forKey: .unplaced)
        try container.encodeIfPresent(days, forKey: .days)
        try container.encodeIfPresent(legs, forKey: .legs)
        try container.encodeIfPresent(solutionStatus, forKey: .solutionStatus)
        try container.encodeIfPresent(costTotal, forKey: .costTotal)
        try container.encodeIfPresent(costCurrency, forKey: .costCurrency)
        try container.encodeIfPresent(costUnknownCount, forKey: .costUnknownCount)
    }

    var dayList: [PlanDay] { days ?? [] }

    var unplacedList: [UnplacedVisit] { unplaced ?? [] }

    var displayDayCount: Int { dayList.count }

    var displayStopCount: Int { dayList.reduce(0) { $0 + $1.stops.count } }

    var versionLabel: String { "v\(versionNumber ?? 0)" }

    var feasibilityLabel: String {
        guard let feasible else { return "未知" }
        if !feasible { return "不可行" }
        return (degraded ?? false) ? "可行（降级求解）" : "可行"
    }

    /// Ordered `(day, stop)` pairs used by the map and the visit workspace.
    var orderedStops: [(day: PlanDay, stop: PlanStop)] {
        dayList.flatMap { day in day.stops.map { (day: day, stop: $0) } }
    }

    var totalTravelMinutes: Int {
        dayList.reduce(0) { partial, day in
            partial + day.stops.reduce(0) { $0 + ($1.travelMinutes ?? 0) }
        }
    }

    var solutionStatusLabel: String {
        switch (solutionStatus ?? "FEASIBLE").uppercased() {
        case "OPTIMAL": return "最优"
        case "FEASIBLE": return "可行（未证明最优）"
        case "PARTIAL": return "部分完成"
        case "INFEASIBLE": return "不可行"
        case "UNKNOWN": return "未知"
        case "ERROR": return "求解错误"
        default: return solutionStatus ?? "未知"
        }
    }

    var costLabel: String {
        guard let costTotal else {
            return (costUnknownCount ?? 0) > 0 ? "费用未知（\(costUnknownCount ?? 0) 段）" : "无费用数据"
        }
        return "\(costCurrency ?? "") \(String(format: "%.2f", costTotal))"
    }

    var totalDistanceKm: Double {
        dayList.reduce(0) { partial, day in
            partial + day.stops.reduce(0) { $0 + ($1.distanceKm ?? 0) }
        }
    }
}

/// `POST /v1/trips/{id}/plans/{plan_id}:activate` response.
struct PlanActivation: Codable, Equatable {
    var tripId: String
    var activePlanVersionId: String
    var tripVersion: Int
    var previousPlanVersionId: String?
    var activatedAt: Date?

    init(
        tripId: String,
        activePlanVersionId: String,
        tripVersion: Int,
        previousPlanVersionId: String? = nil,
        activatedAt: Date? = nil
    ) {
        self.tripId = tripId
        self.activePlanVersionId = activePlanVersionId
        self.tripVersion = tripVersion
        self.previousPlanVersionId = previousPlanVersionId
        self.activatedAt = activatedAt
    }

    private enum CodingKeys: String, CodingKey {
        case tripId
        case activePlanVersionId = "planVersionId"
        case tripVersion
        case previousPlanVersionId = "supersededPlanVersionId"
        case activatedAt
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.tripId = try container.decode(String.self, forKey: .tripId)
        self.activePlanVersionId = try container.decode(String.self, forKey: .activePlanVersionId)
        self.tripVersion = try container.decodeIfPresent(Int.self, forKey: .tripVersion) ?? 1
        self.previousPlanVersionId = try container.decodeIfPresent(
            String.self, forKey: .previousPlanVersionId
        )
        self.activatedAt = try container.decodeIfPresent(Date.self, forKey: .activatedAt)
    }

    var switchedFrom: String {
        previousPlanVersionId ?? "（首次激活）"
    }
}
