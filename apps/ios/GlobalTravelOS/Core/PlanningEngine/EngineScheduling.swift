import Foundation

/// 确定性日程构造（直译 scheduling.py）：给定访问顺序，贪心排程并物化到分钟。
///
/// 多天连续性语义（冻结）：整条行程是一条连续路径，天指针只增不减；
/// 求解器只决定顺序，时间与日归属由此模块统一物化。
enum EngineScheduling {
    struct DayState {
        var window: EngineDays.DayWindow
        var stops: [EngineStopPlan] = []

        var fullAt: Int { stops.count }
    }

    struct ScheduleOutcome: Equatable {
        var days: [EngineDayPlan]
        var legs: [EngineTravelLegPlan]
        var unplaced: [EngineUnplacedStop]
        var travelMinutesTotal: Int
        var valuePlaced: Double
    }

    /// 在 earliest 之后选最早的可行到达时刻（到达须落在窗口区间内）。
    static func resolveArrival(
        earliest: Date, stop: EngineStopInput, windowConflict: inout Bool
    ) -> Date? {
        if stop.timeWindows.isEmpty { return earliest }
        for window in stop.timeWindows {
            let candidate = earliest > window.start ? earliest : window.start
            if candidate <= window.end { return candidate }
        }
        windowConflict = true
        return nil
    }

    static func buildSchedule(
        _ problem: EnginePlanningProblem, _ orderedStopIds: [UUID]
    ) -> ScheduleOutcome {
        var byId: [UUID: EngineStopInput] = [:]
        for stop in problem.stops { byId[stop.visitId] = stop }
        var estimates: [String: EngineTravelEstimate] = [:]
        for item in problem.travelEstimates {
            let key = "\(item.fromVisitId?.uuidString ?? "nil")|\(item.toVisitId.uuidString)|\(item.mode.rawValue)"
            estimates[key] = item
        }
        let dayWindows = (try? EngineDays.enumerateDays(
            window: problem.window, timezoneHome: problem.timezoneHome
        )) ?? []
        var states = dayWindows.map { DayState(window: $0) }

        let maxPerDay = problem.constraints.maxStopsPerDay
        var travelTotal = 0
        var valuePlaced = 0.0
        var legs: [EngineTravelLegPlan] = []
        var unplaced: [EngineUnplacedStop] = []
        var seen = Set<UUID>()

        func coords(_ visitId: UUID) -> (Double, Double) {
            guard let stop = byId[visitId] else { return (problem.origin.lat, problem.origin.lon) }
            return (stop.lat, stop.lon)
        }

        // 第 index 天的出发位置：回溯最近一个有停靠的日子（空日不缓存）。
        func dayStart(_ index: Int) -> (Double, Double, UUID?) {
            if index > 0 {
                for earlier in stride(from: index - 1, through: 0, by: -1) {
                    let earlierStops = states[earlier].stops
                    if let last = earlierStops.last {
                        let (lat, lon) = coords(last.visitId)
                        return (lat, lon, last.visitId)
                    }
                }
            }
            return (problem.origin.lat, problem.origin.lon, nil)
        }

        // 当天续排的出发时刻与位置。
        func departure(_ index: Int) -> (Date, Double, Double, UUID?) {
            let placed = states[index].stops
            if let last = placed.last {
                let (lat, lon) = coords(last.visitId)
                return (last.depart, lat, lon, last.visitId)
            }
            let (lat, lon, fromVisit) = dayStart(index)
            return (states[index].window.open, lat, lon, fromVisit)
        }

        var pointer = 0

        for visitId in orderedStopIds {
            if seen.contains(visitId) { continue }
            guard let stop = byId[visitId] else { continue }

            var windowConflict = false
            let hasRoom = states.contains { $0.fullAt < maxPerDay }
            var placed = false

            if pointer < states.count {
                for index in pointer..<states.count {
                    if states[index].fullAt >= maxPerDay { continue }

                    let (departTime, lat, lon, fromVisit) = departure(index)
                    let estimateKey = "\(fromVisit?.uuidString ?? "nil")|\(stop.visitId.uuidString)|\(stop.movementMode.rawValue)"
                    let distance: Double
                    let travel: Int
                    var costAmount: Double?
                    var costCurrency: String?
                    var source = "haversine-estimate"
                    var stale = false
                    if let estimate = estimates[estimateKey] {
                        distance = estimate.distanceKm
                        travel = estimate.travelMinutes
                        costAmount = estimate.costAmount
                        costCurrency = estimate.costCurrency
                        source = estimate.source
                        stale = estimate.stale
                    } else {
                        distance = EngineGeometry.haversineKm(
                            lat1: lat, lon1: lon, lat2: stop.lat, lon2: stop.lon
                        )
                        travel = EngineGeometry.travelMinutes(
                            lat1: lat, lon1: lon, lat2: stop.lat, lon2: stop.lon,
                            mode: stop.movementMode
                        )
                    }
                    let earliest = EngineDays.addElapsed(departTime, minutes: travel)
                    guard let arrive = resolveArrival(
                        earliest: earliest, stop: stop, windowConflict: &windowConflict
                    ) else { continue }

                    let depart = EngineDays.addElapsed(arrive, minutes: stop.serviceMinutes)
                    if depart > states[index].window.close { continue }

                    states[index].stops.append(
                        EngineStopPlan(
                            visitId: stop.visitId,
                            sequence: states[index].stops.count + 1,
                            arrive: arrive,
                            depart: depart,
                            travelMinutes: travel
                        )
                    )
                    legs.append(
                        EngineTravelLegPlan(
                            fromVisitId: fromVisit,
                            toVisitId: stop.visitId,
                            dayIndex: index,
                            mode: stop.movementMode,
                            travelMinutes: travel,
                            distanceKm: (distance * 1000).rounded() / 1000,
                            costAmount: costAmount,
                            costCurrency: costCurrency,
                            source: source,
                            stale: stale
                        )
                    )
                    travelTotal += travel
                    valuePlaced += stop.businessValue
                    seen.insert(visitId)
                    placed = true
                    pointer = index
                    break
                }
            }

            if placed { continue }

            let reason: EngineUnplacedReason
            if !hasRoom {
                reason = .capacityExceeded
            } else if windowConflict {
                reason = .timeWindowInfeasible
            } else {
                reason = .noFeasibleSlot
            }
            unplaced.append(EngineUnplacedStop(visitId: visitId, reason: reason))
        }

        let days = states.map { EngineDayPlan(date: $0.window.date, stops: $0.stops) }
        return ScheduleOutcome(
            days: days, legs: legs, unplaced: unplaced,
            travelMinutesTotal: travelTotal, valuePlaced: valuePlaced
        )
    }

    /// 目标值定义（唯一口径）：越小越好。
    static func makeObjectiveValue(
        _ problem: EnginePlanningProblem, _ travelTotal: Int, _ valuePlaced: Double
    ) -> Double {
        let weights = problem.objectiveWeights
        let raw = Double(travelTotal) * weights.travel - valuePlaced * weights.value
        return (raw * 1_000_000).rounded() / 1_000_000
    }

    static func deferredToNextDay(_ problem: EnginePlanningProblem, stop: EngineStopInput) -> Bool {
        let days = (try? EngineDays.enumerateDays(
            window: problem.window, timezoneHome: problem.timezoneHome
        )) ?? []
        return days.count > 1
    }
}
