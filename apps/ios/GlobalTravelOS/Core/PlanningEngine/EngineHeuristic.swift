import Foundation

/// 确定性启发式求解器（直译 solvers/heuristic.py）：
/// 候选枚举 + 有界相邻交换。无随机、无墙钟，跨机一致。
enum EngineHeuristic {
    static let passesPerBudgetSecond = 5
    static let maxPasses = 50

    static func score(
        _ problem: EnginePlanningProblem, _ outcome: EngineScheduling.ScheduleOutcome,
        _ order: [UUID]
    ) -> (Double, [String]) {
        let objective = EngineScheduling.makeObjectiveValue(
            problem, outcome.travelMinutesTotal, outcome.valuePlaced
        )
        return (objective, order.map { $0.uuidString })
    }

    static func scoreLess(_ a: (Double, [String]), _ b: (Double, [String])) -> Bool {
        if a.0 != b.0 { return a.0 < b.0 }
        return lexLess(a.1, b.1)
    }

    static func lexLess(_ a: [String], _ b: [String]) -> Bool {
        for i in 0..<min(a.count, b.count) {
            if a[i] != b[i] { return a[i] < b[i] }
        }
        return a.count < b.count
    }

    /// 相邻交换改进：不交换两个锁定停靠（保 LOCKED_ORDER）。
    static func improve(
        _ problem: EnginePlanningProblem,
        _ order: [UUID],
        _ outcome: EngineScheduling.ScheduleOutcome
    ) -> ([UUID], EngineScheduling.ScheduleOutcome) {
        let locked = Set(problem.constraints.lockedOrder)
        let size = order.count
        guard size >= 2 else { return (order, outcome) }

        let passes = max(1, min(problem.solverBudgetSeconds * passesPerBudgetSecond, maxPasses))
        var bestOrder = order
        var bestOutcome = outcome
        var bestScore = score(problem, outcome, order)

        for _ in 0..<passes {
            var improved = false
            for index in 0..<(size - 1) {
                let left = bestOrder[index]
                let right = bestOrder[index + 1]
                if locked.contains(left) && locked.contains(right) { continue }
                var candidate = bestOrder
                candidate.swapAt(index, index + 1)
                let candidateOutcome = EngineScheduling.buildSchedule(problem, candidate)
                let candidateScore = score(problem, candidateOutcome, candidate)
                if scoreLess(candidateScore, bestScore) {
                    bestScore = candidateScore
                    bestOrder = candidate
                    bestOutcome = candidateOutcome
                    improved = true
                }
            }
            if !improved { break }
        }
        return (bestOrder, bestOutcome)
    }

    static func solve(
        _ problem: EnginePlanningProblem, _ eligible: [UUID]
    ) throws -> EngineSolveOutcome {
        let orders = EngineCandidates.candidateOrders(problem, eligible)

        var bestOrder: [UUID] = []
        var bestOutcome: EngineScheduling.ScheduleOutcome?
        var bestScore: (Double, [String])?

        for order in orders {
            let outcome = EngineScheduling.buildSchedule(problem, order)
            let current = score(problem, outcome, order)
            if bestScore == nil || scoreLess(current, bestScore!) {
                bestScore = current
                bestOrder = order
                bestOutcome = outcome
            }
        }

        if bestScore == nil || bestOutcome == nil {
            bestOrder = eligible
            bestOutcome = EngineScheduling.buildSchedule(problem, eligible)
        }

        let (improvedOrder, improvedOutcome) = improve(problem, bestOrder, bestOutcome!)

        return EngineSolveOutcome(
            solver: .heuristic,
            days: improvedOutcome.days,
            legs: improvedOutcome.legs,
            unplaced: improvedOutcome.unplaced,
            travelMinutesTotal: improvedOutcome.travelMinutesTotal,
            valuePlaced: improvedOutcome.valuePlaced,
            objectiveValue: EngineScheduling.makeObjectiveValue(
                problem, improvedOutcome.travelMinutesTotal, improvedOutcome.valuePlaced
            ),
            degraded: false
        )
    }
}
