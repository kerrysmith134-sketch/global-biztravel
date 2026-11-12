import Foundation

/// 独立验证器（直译 validator.py）：不复用求解器/调度器逻辑，逐条独立复算。
///
/// 设计约束（不变量 I1）：任何一条规则 FAIL ⇒ feasible = False；
/// "不可行"永远不可能被伪装成"可行"。Swift 侧以独立类型与本文件内实现
/// 强隔离（不调用 EngineScheduling 的任何函数）。
enum EngineValidator {
    static func elapsedMinutes(start: Date, end: Date) -> Int {
        // 独立复算真实经过时间（Date 即瞬时；不借用 EngineDays）。
        Int(end.timeIntervalSince(start) / 60.0)
    }

    /// 独立复算逐日可用区间（第二份实现）。
    static func dayBounds(_ problem: EnginePlanningProblem) -> [Date: (Date, Date)] {
        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = TimeZone(identifier: problem.timezoneHome) ?? .current
        var bounds: [Date: (Date, Date)] = [:]
        let start = problem.window.start
        let end = problem.window.end
        var cursor = calendar.startOfDay(for: start)
        let last = calendar.startOfDay(for: end)
        while cursor <= last {
            guard let nextMidnight = calendar.date(byAdding: .day, value: 1, to: cursor) else { break }
            let midnight = cursor
            let dayOpen = start > midnight ? start : midnight
            var dayClose = nextMidnight.addingTimeInterval(-1)
            if end < dayClose { dayClose = end }
            bounds[cursor] = (dayOpen, dayClose)
            guard let advanced = calendar.date(byAdding: .day, value: 1, to: cursor) else { break }
            cursor = advanced
        }
        return bounds
    }

    static func check(_ rule: String, _ ok: Bool, detail: String? = nil) -> EngineValidationCheck {
        EngineValidationCheck(
            rule: rule, status: ok ? .pass_ : .fail, detail: detail
        )
    }

    static func validate(
        problem: EnginePlanningProblem,
        days: [EngineDayPlan],
        unplaced: [EngineUnplacedStop],
        objectiveValue: Double
    ) -> EngineValidationReport {
        var checks: [EngineValidationCheck] = []

        var byId: [UUID: EngineStopInput] = [:]
        for stop in problem.stops { byId[stop.visitId] = stop }
        let allIds = Set(byId.keys)
        let placedIds = days.flatMap { $0.stops.map { $0.visitId } }
        let unplacedIds = unplaced.map { $0.visitId }
        let immovable = Set(problem.constraints.immovableStopIds)

        // 1. STOP_COVERAGE
        let placedSet = Set(placedIds)
        let unplacedSet = Set(unplacedIds)
        let missing = allIds.subtracting(placedSet).subtracting(unplacedSet).map { $0.uuidString }.sorted()
        let overlap = placedSet.intersection(unplacedSet).map { $0.uuidString }.sorted()
        let immovableMissing = immovable.subtracting(placedSet).map { $0.uuidString }.sorted()
        let unknownPlaced = placedSet.subtracting(allIds).map { $0.uuidString }.sorted()
        let coverageOk = missing.isEmpty && overlap.isEmpty
            && immovableMissing.isEmpty && unknownPlaced.isEmpty
        checks.append(check(
            EngineRule.stopCoverage, coverageOk,
            detail: coverageOk ? nil
                : "missing=\(missing) overlap=\(overlap) immovable_unplaced=\(immovableMissing) unknown=\(unknownPlaced)"
        ))

        // 2. NO_DUPLICATE_STOP
        var counted: [UUID: Int] = [:]
        for id in placedIds { counted[id, default: 0] += 1 }
        let duplicates = counted.filter { $0.value > 1 }.keys.map { $0.uuidString }.sorted()
        checks.append(check(
            EngineRule.noDuplicateStop, duplicates.isEmpty,
            detail: duplicates.isEmpty ? nil : "duplicated=\(duplicates)"
        ))

        // 3. DAY_WITHIN_WINDOW
        let bounds = dayBounds(problem)
        var dayIssues: [String] = []
        var seenDates = Set<Date>()
        for day in days {
            if seenDates.contains(day.date) {
                dayIssues.append("\(day.date):duplicate-day")
            }
            seenDates.insert(day.date)
            guard let window = bounds[day.date] else {
                dayIssues.append("\(day.date):outside-trip-window")
                continue
            }
            for stop in day.stops {
                if !(window.0 <= stop.arrive && stop.arrive <= window.1) {
                    dayIssues.append("\(day.date):\(stop.visitId):arrive-outside-day")
                }
                if !(window.0 <= stop.depart && stop.depart <= window.1) {
                    dayIssues.append("\(day.date):\(stop.visitId):depart-outside-day")
                }
                if stop.depart < stop.arrive {
                    dayIssues.append("\(day.date):\(stop.visitId):depart-before-arrive")
                }
            }
        }
        checks.append(check(
            EngineRule.dayWithinWindow, dayIssues.isEmpty,
            detail: dayIssues.isEmpty ? nil : dayIssues.prefix(10).joined(separator: ";")
        ))

        // 4. TIME_WINDOW
        var windowIssues: [String] = []
        for day in days {
            for stop in day.stops {
                guard let source = byId[stop.visitId], !source.timeWindows.isEmpty else { continue }
                if !source.timeWindows.contains(where: { $0.start <= stop.arrive && stop.arrive <= $0.end }) {
                    windowIssues.append("\(stop.visitId):arrive=\(stop.arrive)")
                }
            }
        }
        checks.append(check(
            EngineRule.timeWindow, windowIssues.isEmpty,
            detail: windowIssues.isEmpty ? nil : windowIssues.prefix(10).joined(separator: ";")
        ))

        // 5. SERVICE_DURATION
        var serviceIssues: [String] = []
        for day in days {
            for stop in day.stops {
                guard let source = byId[stop.visitId] else { continue }
                let actual = elapsedMinutes(start: stop.arrive, end: stop.depart)
                if actual != source.serviceMinutes {
                    serviceIssues.append("\(stop.visitId):expected=\(source.serviceMinutes) actual=\(actual)")
                }
            }
        }
        checks.append(check(
            EngineRule.serviceDuration, serviceIssues.isEmpty,
            detail: serviceIssues.isEmpty ? nil : serviceIssues.prefix(10).joined(separator: ";")
        ))

        // 6. TRAVEL_TIME_CONSISTENCY —— 整条路径连续链路独立复算
        var estimates: [String: EngineTravelEstimate] = [:]
        for item in problem.travelEstimates {
            let key = "\(item.fromVisitId?.uuidString ?? "nil")|\(item.toVisitId.uuidString)|\(item.mode.rawValue)"
            estimates[key] = item
        }
        var travelIssues: [String] = []
        var prevLat = problem.origin.lat
        var prevLon = problem.origin.lon
        var prevVisitId: UUID?
        for day in days.sorted(by: { $0.date < $1.date }) {
            for stop in day.stops {
                guard let source = byId[stop.visitId] else {
                    travelIssues.append("\(stop.visitId):unknown-stop")
                    continue
                }
                let key = "\(prevVisitId?.uuidString ?? "nil")|\(source.visitId.uuidString)|\(source.movementMode.rawValue)"
                let expected: Int
                if let estimate = estimates[key] {
                    expected = estimate.travelMinutes
                } else {
                    expected = EngineGeometry.travelMinutes(
                        lat1: prevLat, lon1: prevLon, lat2: source.lat, lon2: source.lon,
                        mode: source.movementMode
                    )
                }
                if stop.travelMinutes != expected {
                    travelIssues.append("\(stop.visitId):expected=\(expected) actual=\(stop.travelMinutes)")
                }
                prevLat = source.lat
                prevLon = source.lon
                prevVisitId = source.visitId
            }
        }
        checks.append(check(
            EngineRule.travelTimeConsistency, travelIssues.isEmpty,
            detail: travelIssues.isEmpty ? nil : travelIssues.prefix(10).joined(separator: ";")
        ))

        // 7. MAX_STOPS_PER_DAY
        let limit = problem.constraints.maxStopsPerDay
        let overLimit = days
            .filter { $0.stops.count > limit }
            .map { "\($0.date):\($0.stops.count)>\(limit)" }
        checks.append(check(
            EngineRule.maxStopsPerDay, overLimit.isEmpty,
            detail: overLimit.isEmpty ? nil : overLimit.joined(separator: ";")
        ))

        // 8. LOCKED_ORDER
        var arrivalById: [UUID: Date] = [:]
        for day in days { for stop in day.stops { arrivalById[stop.visitId] = stop.arrive } }
        var orderIssues: [String] = []
        let locked = problem.constraints.lockedOrder
        for index in 0..<max(0, locked.count - 1) {
            let first = locked[index]
            let second = locked[index + 1]
            let firstArrive = arrivalById[first]
            let secondArrive = arrivalById[second]
            if secondArrive != nil && firstArrive == nil {
                orderIssues.append("\(second):placed-before-\(first)")
                continue
            }
            if let firstArrive, let secondArrive, secondArrive < firstArrive {
                orderIssues.append("\(first)->\(second):order-violated")
            }
        }
        checks.append(check(
            EngineRule.lockedOrder, orderIssues.isEmpty,
            detail: orderIssues.isEmpty ? nil : orderIssues.prefix(10).joined(separator: ";")
        ))

        // 9. OBJECTIVE_RECOMPUTED
        let weights = problem.objectiveWeights
        let recomputedTravel = days.flatMap { $0.stops }.reduce(0) { $0 + $1.travelMinutes }
        let recomputedValue = days.flatMap { $0.stops }
            .reduce(0.0) { sum, stop in
                sum + (byId[stop.visitId]?.businessValue ?? 0.0)
            }
        let recomputed = ((Double(recomputedTravel) * weights.travel - recomputedValue * weights.value) * 1_000_000).rounded() / 1_000_000
        let objectiveOk = abs(recomputed - objectiveValue) <= 1e-6
        checks.append(check(
            EngineRule.objectiveRecomputed, objectiveOk,
            detail: objectiveOk ? nil : "expected=\(recomputed) reported=\(objectiveValue)"
        ))

        return EngineValidationReport(
            feasible: checks.allSatisfy { $0.status == .pass_ }, checks: checks
        )
    }
}
