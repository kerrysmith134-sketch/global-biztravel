import Foundation

/// 领域 DTO ⇄ 引擎契约转换（本地模式，对应服务端 planning_service.py 的转换职责）。
enum EngineMapping {
    static let dateFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd"
        formatter.locale = Locale(identifier: "en_US_POSIX")
        return formatter
    }()

    /// 行程 + 拜访 + 地点 → 引擎规划问题。
    /// 拜访坐标从地点表查（visits 本身无坐标列，与服务端联查语义一致）。
    static func makeProblem(
        trip: Trip, visits: [TripVisit], places: [Place],
        budgetSeconds: Int
    ) -> EnginePlanningProblem {
        var placeById: [String: Place] = [:]
        for place in places { placeById[place.id ?? ""] = place }

        var stops: [EngineStopInput] = []
        for (index, visit) in visits.enumerated() {
            let place = visit.placeId.flatMap { placeById[$0] }
            guard let place else { continue } // 无地点的拜访不进规划（显式缺口语义）
            let windows: [EngineTimeWindow]
            if let start = visit.timeWindowStart, let end = visit.timeWindowEnd {
                windows = [EngineTimeWindow(start: start, end: end)]
            } else {
                windows = []
            }
            stops.append(
                EngineStopInput(
                    visitId: visitUUID(visit.id, fallbackIndex: index),
                    placeId: UUID(uuidString: place.id ?? "") ?? UUID(),
                    lat: place.latitude ?? 0,
                    lon: place.longitude ?? 0,
                    serviceMinutes: visit.serviceMinutes ?? 60,
                    timeWindows: windows,
                    priority: visit.priority ?? 2,
                    businessValue: 0,
                    movementMode: EngineMovementMode(rawValue: visit.movementMode ?? "GROUND") ?? .ground
                )
            )
        }

        let origin = trip.originPlace
        return EnginePlanningProblem(
            tripId: tripUUID(trip.id),
            timezoneHome: origin?.timezone ?? "Asia/Shanghai",
            origin: EngineOrigin(
                placeId: UUID(uuidString: trip.originPlaceId ?? "") ?? UUID(),
                lat: origin?.latitude ?? 0,
                lon: origin?.longitude ?? 0
            ),
            window: EngineProblemWindow(
                start: trip.window?.start ?? Date(),
                end: trip.window?.end ?? Date().addingTimeInterval(86_400)
            ),
            stops: stops,
            constraints: EngineConstraints(maxStopsPerDay: 4),
            solverBudgetSeconds: budgetSeconds
        )
    }

    /// visit.id / trip.id 可能不是合法 UUID（本地 UUID().uuidString 大写）——
    /// 引擎 UUID 统一用小写重解析，失败则从内容派生确定性 UUID。
    static func visitUUID(_ id: String, fallbackIndex: Int) -> UUID {
        if let uuid = UUID(uuidString: id.lowercased()) { return uuid }
        var bytes = [UInt8](repeating: 0, count: 16)
        for (index, byte) in Array(id.utf8.prefix(16)).enumerated() { bytes[index] = byte }
        return UUID(uuid: (
            bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6], bytes[7],
            bytes[8], bytes[9], bytes[10], bytes[11], bytes[12], bytes[13], bytes[14], bytes[15]
        ))
    }

    static func tripUUID(_ id: String) -> UUID {
        visitUUID(id, fallbackIndex: 0)
    }

    /// 引擎日计划 → UI PlanDay（含地点名称增强）。
    static func makePlanDays(
        from days: [EngineDayPlan], places: [Place], visits: [TripVisit]
    ) -> [PlanDay] {
        var visitPlaceId: [UUID: String] = [:]
        for visit in visits {
            visitPlaceId[visitUUID(visit.id, fallbackIndex: 0)] = visit.placeId
        }
        var placeById: [String: Place] = [:]
        for place in places { placeById[place.id ?? ""] = place }

        return days.map { day in
            let stops = day.stops.map { stop in
                let placeId = visitPlaceId[stop.visitId]
                let place = placeId.flatMap { placeById[$0] }
                return PlanStop(
                    visitId: stop.visitId.uuidString,
                    sequence: stop.sequence,
                    placeId: placeId,
                    placeName: place?.name,
                    city: place?.city,
                    countryCode: place?.countryCode,
                    latitude: place?.latitude,
                    longitude: place?.longitude,
                    arrive: stop.arrive,
                    depart: stop.depart,
                    travelMinutes: stop.travelMinutes,
                    distanceKm: nil
                )
            }
            return PlanDay(date: dateFormatter.string(from: day.date), stops: stops)
        }
    }

    /// 引擎 legs → UI PlanTravelLeg。
    static func makeLegs(from legs: [EngineTravelLegPlan]) -> [PlanTravelLeg] {
        legs.enumerated().map { index, leg in
            PlanTravelLeg(
                id: "\(leg.fromVisitId?.uuidString ?? "origin")-\(leg.toVisitId.uuidString)-\(index)",
                fromStopId: leg.fromVisitId?.uuidString,
                toStopId: leg.toVisitId.uuidString,
                dayIndex: leg.dayIndex,
                mode: leg.mode.rawValue,
                travelMinutes: leg.travelMinutes,
                distanceKm: leg.distanceKm,
                costAmount: leg.costAmount,
                costCurrency: leg.costCurrency,
                source: leg.source,
                stale: leg.stale
            )
        }
    }

    /// 引擎验证报告 → UI ValidationReport。
    static func makeValidationReport(
        from report: EngineValidationReport
    ) -> ValidationReport {
        ValidationReport(
            feasible: report.feasible,
            checks: report.checks.map {
                ValidationCheck(
                    rule: $0.rule,
                    status: ValidationCheckStatus(
                        rawValue: $0.status == .pass_ ? "PASS" : "FAIL"
                    ),
                    detail: $0.detail
                )
            },
            violations: nil
        )
    }

    /// 引擎 unplaced → UI UnplacedVisit。
    static func makeUnplaced(
        from unplaced: [EngineUnplacedStop], places: [Place], visits: [TripVisit]
    ) -> [UnplacedVisit] {
        var visitPlaceId: [UUID: String] = [:]
        for visit in visits {
            visitPlaceId[visitUUID(visit.id, fallbackIndex: 0)] = visit.placeId
        }
        var placeById: [String: Place] = [:]
        for place in places { placeById[place.id ?? ""] = place }
        return unplaced.map { item in
            let placeId = visitPlaceId[item.visitId]
            let name = placeId.flatMap { placeById[$0] }?.name
            return UnplacedVisit(
                visitId: item.visitId.uuidString, placeName: name, reason: item.reason.rawValue
            )
        }
    }
}

extension JSONDecoder {
    /// 本地模式 DTO 解码（snake_case + ISO8601 双格式，与服务端契约一致）。
    static var local: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let raw = try container.decode(String.self)
            if let date = ISO8601.date(from: raw) { return date }
            if let date = ISO8601.day(from: raw) { return date }
            throw DecodingError.dataCorruptedError(
                in: container, debugDescription: "Unsupported ISO-8601 value: \(raw)"
            )
        }
        return decoder
    }
}

extension JSONEncoder {
    /// 本地模式 DTO 编码（snake_case + ISO8601）。
    static var local: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }
}
