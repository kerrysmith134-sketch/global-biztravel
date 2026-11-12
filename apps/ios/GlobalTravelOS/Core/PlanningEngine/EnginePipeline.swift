import Foundation

/// 六段式流水线（直译 pipeline.py 的本地路径）：预检 → 候选 → 启发式求解 → 独立验证 → 状态判定。
/// 本地模式只有启发式求解器（ortools 无 iOS 绑定）；兜底链固定为 (heuristic,)。
enum EnginePipeline {
    struct Result: Codable, Equatable {
        var tripId: UUID
        var problemHash: String
        var solver: EngineSolverKind
        var days: [EngineDayPlan]
        var legs: [EngineTravelLegPlan]
        var objectiveValue: Double
        var validation: EngineValidationReport
        var unplaced: [EngineUnplacedStop]
        var status: EngineSolutionStatus
        var degraded: Bool

        var placedVisitIds: [UUID] {
            days.flatMap { $0.stops.map { $0.visitId } }
        }
    }

    /// 内容寻址哈希：相同问题必得相同哈希（与 Python 端口径一致——规范化 JSON + sha256）。
    static func problemHash(_ problem: EnginePlanningProblem) -> String {
        let canonical = canonicalProblemPayload(problem)
        let digest = _sha256(Data(canonical.utf8))
        return "sha256:" + digest.map { byte in String(format: "%02x", byte) }.joined()
    }

    /// 规划主入口：`plan_trip` 的本地等价物。
    /// 抛 `EngineError.noSolverProducedFullCoverage`（物化后覆盖不满 ⇒ 求解失败，诚实语义）。
    static func planTrip(
        _ problem: EnginePlanningProblem, solver: EngineSolverKind = .heuristic
    ) throws -> Result {
        // 1. 预检
        let feasibility = try EngineFeasibility.checkFeasibility(problem)
        let eligible = feasibility.eligibleStopIds

        // 2. 求解（本地仅启发式；显式 ortools 请求同样落启发式并标 degraded）
        let outcome: EngineSolveOutcome
        if solver == .ortools {
            var heuristicOutcome = try EngineHeuristic.solve(problem, eligible)
            heuristicOutcome.degraded = true
            outcome = heuristicOutcome
        } else {
            outcome = try EngineHeuristic.solve(problem, eligible)
        }

        // 3. 覆盖不变量：物化后仍有人没排进 ⇒ 求解失败（不允许静默半成品）
        let materialized = outcome.days.flatMap { $0.stops.map { $0.visitId } }
        let missing = eligible.filter { !materialized.contains($0) }
        if !missing.isEmpty {
            throw EngineError.noSolverProducedFullCoverage(
                "\(outcome.solver.rawValue): \(missing.count)/\(eligible.count) 个 eligible 停靠未被排入"
            )
        }

        // 4. 合并预检剔除
        var unplaced = feasibility.preUnplaced
        unplaced.append(contentsOf: outcome.unplaced)

        // 5. 独立验证
        let report = EngineValidator.validate(
            problem: problem,
            days: outcome.days,
            unplaced: unplaced,
            objectiveValue: outcome.objectiveValue
        )

        // 6. 状态判定
        let status: EngineSolutionStatus
        if !report.feasible {
            status = .infeasible
        } else if !unplaced.isEmpty {
            status = .partial
        } else {
            status = .feasible
        }

        return Result(
            tripId: problem.tripId,
            problemHash: problemHash(problem),
            solver: outcome.solver,
            days: outcome.days,
            legs: outcome.legs,
            objectiveValue: outcome.objectiveValue,
            validation: report,
            unplaced: unplaced,
            status: status,
            degraded: outcome.degraded
        )
    }

    // MARK: - 哈希

    /// 与 Python 端 canonical JSON 同序：键排序 + 紧凑分隔符。
    static func canonicalProblemPayload(_ problem: EnginePlanningProblem) -> String {
        // 用 Codable 编码后做键排序的规范化 JSON（递归）。
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        guard let data = try? encoder.encode(problem),
              let object = try? JSONSerialization.jsonObject(with: data) else {
            return "{}"
        }
        return canonicalJSON(object)
    }

    /// 递归规范化：字典键排序输出（模拟 Python json.dumps(sort_keys=True)）。
    static func canonicalJSON(_ value: Any) -> String {
        if let dictionary = value as? [String: Any] {
            let items = dictionary.keys.sorted().map { key in
                "\"\(key)\":\(canonicalJSON(dictionary[key] ?? "null"))"
            }
            return "{" + items.joined(separator: ",") + "}"
        }
        if let array = value as? [Any] {
            return "[" + array.map(canonicalJSON).joined(separator: ",") + "]"
        }
        if let number = value as? NSNumber {
            // 布尔在 JSONSerialization 里也是 NSNumber——区分输出 true/false。
            if CFGetTypeID(number) == CFBooleanGetTypeID() {
                return number.boolValue ? "true" : "false"
            }
            return number.stringValue
        }
        if let string = value as? String {
            let escaped = string
                .replacingOccurrences(of: "\\", with: "\\\\")
                .replacingOccurrences(of: "\"", with: "\\\"")
            return "\"\(escaped)\""
        }
        if let bool = value as? Bool { return bool ? "true" : "false" }
        if value is NSNull { return "null" }
        return "null"
    }

    static func _sha256(_ data: Data) -> [UInt8] {
        // CommonCrypto 在 iOS 不可直接 import Crypto（SwiftCrypto 非系统）；
        // 用 CryptoKit（iOS 13+ 系统框架，零依赖）。
        let digest = CryptoDigest.sha256(data)
        return digest
    }
}

import CryptoKit

enum CryptoDigest {
    static func sha256(_ data: Data) -> [UInt8] {
        Array(SHA256.hash(data: data))
    }
}
