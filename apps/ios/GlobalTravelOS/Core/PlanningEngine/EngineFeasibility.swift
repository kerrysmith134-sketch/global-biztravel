import Foundation

/// 预检（直译 feasibility.py）：进入求解器前剔除确定不可排的停靠。
enum EngineFeasibility {
    struct Outcome: Equatable {
        var eligibleStopIds: [UUID]
        var preUnplaced: [EngineUnplacedStop]
        var dayCount: Int
    }

    /// 确定性排序键：优先级升序 → 价值降序 → visitId 字典序。
    static func rankKey(_ stop: EngineStopInput) -> (Int, Double, String) {
        (stop.priority, -stop.businessValue, stop.visitId.uuidString)
    }

    static func rankKeyLess(_ lhs: (Int, Double, String), _ rhs: (Int, Double, String)) -> Bool {
        if lhs.0 != rhs.0 { return lhs.0 < rhs.0 }
        if lhs.1 != rhs.1 { return lhs.1 < rhs.1 }
        return lhs.2 < rhs.2
    }

    static func windowIntersectsTrip(
        _ problem: EnginePlanningProblem, _ stop: EngineStopInput
    ) -> Bool {
        if stop.timeWindows.isEmpty { return true }
        for window in stop.timeWindows {
            if window.end >= problem.window.start && window.start <= problem.window.end {
                return true
            }
        }
        return false
    }

    static func checkFeasibility(_ problem: EnginePlanningProblem) throws -> Outcome {
        let days = try EngineDays.enumerateDays(
            window: problem.window, timezoneHome: problem.timezoneHome
        )
        let capacity = problem.constraints.maxStopsPerDay * days.count

        var preUnplaced: [EngineUnplacedStop] = []
        var eligible: [EngineStopInput] = []

        for stop in problem.stops {
            if windowIntersectsTrip(problem, stop) {
                eligible.append(stop)
            } else {
                preUnplaced.append(
                    EngineUnplacedStop(visitId: stop.visitId, reason: .timeWindowInfeasible)
                )
            }
        }

        let immovable = Set(problem.constraints.immovableStopIds)
        let required = eligible.filter { immovable.contains($0.visitId) }
        let optional = eligible.filter { !immovable.contains($0.visitId) }

        let keptRequired: [EngineStopInput]
        if required.count > capacity {
            let ordered = required.sorted { rankKeyLess(rankKey($0), rankKey($1)) }
            preUnplaced.append(
                contentsOf: ordered[capacity...].map {
                    EngineUnplacedStop(visitId: $0.visitId, reason: .capacityExceeded)
                }
            )
            keptRequired = Array(ordered[..<capacity])
        } else {
            keptRequired = required
        }

        let room = max(0, capacity - keptRequired.count)
        let droppedOptional = optional
            .sorted { rankKeyLess(rankKey($0), rankKey($1)) }
            .dropFirst(room)
        preUnplaced.append(
            contentsOf: droppedOptional.map {
                EngineUnplacedStop(visitId: $0.visitId, reason: .capacityExceeded)
            }
        )

        let droppedIds = Set(preUnplaced.map { $0.visitId })
        let eligibleIds = problem.stops
            .filter { !droppedIds.contains($0.visitId) }
            .map { $0.visitId }

        // 未排原因按原停靠顺序输出（确定性）。
        preUnplaced.sort {
            stopPosition(problem, $0.visitId) < stopPosition(problem, $1.visitId)
        }

        return Outcome(
            eligibleStopIds: eligibleIds, preUnplaced: preUnplaced, dayCount: days.count
        )
    }

    static func stopPosition(_ problem: EnginePlanningProblem, _ visitId: UUID) -> Int {
        problem.stops.firstIndex { $0.visitId == visitId } ?? problem.stops.count
    }
}

/// 候选序列生成（直译 candidates.py）。
enum EngineCandidates {
    /// 锁定顺序优先 → 不可动 → 其余按 rank_key。
    static func lockAndRankOrder(
        _ problem: EnginePlanningProblem, _ eligible: [UUID]
    ) -> [UUID] {
        let eligibleSet = Set(eligible)
        let locked = problem.constraints.lockedOrder.filter { eligibleSet.contains($0) }
        let lockedSet = Set(locked)
        let immovable = Set(problem.constraints.immovableStopIds)

        let second = eligible
            .filter { immovable.contains($0) && !lockedSet.contains($0) }
            .compactMap { problem.stop(byId: $0) }
            .sorted { EngineFeasibility.rankKeyLess(EngineFeasibility.rankKey($0), EngineFeasibility.rankKey($1)) }
        let third = eligible
            .filter { !immovable.contains($0) && !lockedSet.contains($0) }
            .compactMap { problem.stop(byId: $0) }
            .sorted { EngineFeasibility.rankKeyLess(EngineFeasibility.rankKey($0), EngineFeasibility.rankKey($1)) }

        return locked + second.map { $0.visitId } + third.map { $0.visitId }
    }

    /// 最近邻构造：从当前位置选行程时间最短者；平局按 rank_key。
    static func nearestNeighbourOrder(
        _ problem: EnginePlanningProblem, _ eligible: [UUID]
    ) -> [UUID] {
        var remaining = eligible
        var ordered: [UUID] = []
        var lat = problem.origin.lat
        var lon = problem.origin.lon

        while !remaining.isEmpty {
            var best: UUID?
            var bestTravel = Int.max
            var bestRank: (Int, Double, String)?
            for vid in remaining {
                guard let stop = problem.stop(byId: vid) else { continue }
                let travel = EngineGeometry.travelMinutes(
                    lat1: lat, lon1: lon, lat2: stop.lat, lon2: stop.lon, mode: stop.movementMode
                )
                let rank = EngineFeasibility.rankKey(stop)
                let isBetter = best == nil
                    || travel < bestTravel
                    || (travel == bestTravel && EngineFeasibility.rankKeyLess(rank, bestRank!))
                if isBetter {
                    best = vid
                    bestTravel = travel
                    bestRank = rank
                }
            }
            guard let chosen = best, let stop = problem.stop(byId: chosen) else { break }
            ordered.append(chosen)
            lat = stop.lat
            lon = stop.lon
            remaining.removeAll { $0 == chosen }
        }
        return ordered
    }

    private static func rankLess(
        _ a: (Int, Double, String), _ b: (Int, Double, String)
    ) -> Bool {
        if a.0 != b.0 { return a.0 < b.0 }
        if a.1 != b.1 { return a.1 < b.1 }
        return a.2 < b.2
    }

    /// 去重后的候选顺序集合。
    static func candidateOrders(
        _ problem: EnginePlanningProblem, _ eligible: [UUID]
    ) -> [[UUID]] {
        if eligible.isEmpty { return [[]] }

        let lockedPrefix = lockAndRankOrder(problem, eligible)
        let nnAll = nearestNeighbourOrder(problem, eligible)

        let lockedSet = Set(problem.constraints.lockedOrder)
        let lockedPart = lockedPrefix.filter { lockedSet.contains($0) }
        var candidates: [[UUID]] = [lockedPrefix, nnAll]
        if !lockedPart.isEmpty {
            let lockedSetFull = Set(lockedPart)
            let remaining = eligible.filter { !lockedSetFull.contains($0) }
            let tail = remaining.isEmpty ? [] : nearestNeighbourOrder(problem, remaining)
            candidates.append(lockedPart + tail)
        }

        var seen = Set<[UUID]>()
        var result: [[UUID]] = []
        for order in candidates where !seen.contains(order) {
            seen.insert(order)
            result.append(order)
        }
        return result
    }
}
