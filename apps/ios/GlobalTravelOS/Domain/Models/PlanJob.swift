import Foundation

/// `GET /v1/jobs/{job_id}` → `{id, job_type, status, attempts, result, last_error, created_at, updated_at}`.
///
/// `unknown` exists purely for forward compatibility: polling has to treat a status it has never
/// heard of as “not terminal yet” instead of failing to decode and dropping the job on the floor.
enum JobStatus: String, Codable, Equatable {
    case pending = "PENDING"
    case running = "RUNNING"
    case succeeded = "SUCCEEDED"
    case failed = "FAILED"
    case dead = "DEAD"
    case cancelled = "CANCELLED"
    case unknown = "UNKNOWN"

    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = JobStatus(rawValue: raw) ?? .unknown
    }

    var isTerminal: Bool {
        switch self {
        case .succeeded, .failed, .dead, .cancelled: return true
        case .pending, .running, .unknown: return false
        }
    }

    var isSuccess: Bool { self == .succeeded }

    var label: String {
        switch self {
        case .pending: return "排队中"
        case .running: return "求解中"
        case .succeeded: return "已完成"
        case .failed: return "失败"
        case .dead: return "已终止"
        case .cancelled: return "已取消"
        case .unknown: return "未知"
        }
    }
}

/// `result:{plan_version_id?:String}|null`.
struct JobResult: Codable, Equatable {
    var planVersionId: String?

    init(planVersionId: String? = nil) {
        self.planVersionId = planVersionId
    }

    var hasPlan: Bool {
        !(planVersionId ?? "").isEmpty
    }
}

struct PlanJob: Codable, Equatable, Identifiable {
    var id: String
    var jobType: String?
    var status: JobStatus?
    var attempts: Int?
    var result: JobResult?
    var lastError: String?
    var createdAt: Date?
    var updatedAt: Date?

    init(
        id: String,
        jobType: String? = nil,
        status: JobStatus? = nil,
        attempts: Int? = nil,
        result: JobResult? = nil,
        lastError: String? = nil,
        createdAt: Date? = nil,
        updatedAt: Date? = nil
    ) {
        self.id = id
        self.jobType = jobType
        self.status = status
        self.attempts = attempts
        self.result = result
        self.lastError = lastError
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    var resolvedStatus: JobStatus { status ?? .unknown }

    var producedPlanVersionId: String? {
        guard let value = result?.planVersionId, !value.isEmpty else { return nil }
        return value
    }

    var statusLabel: String { resolvedStatus.label }

    var attemptLabel: String { "尝试 \(attempts ?? 0) 次" }

    var displayError: String? {
        guard let lastError, !lastError.isEmpty else { return nil }
        return lastError
    }
}

/// `POST /v1/trips/{id}/plans:generate` → **202** `{job_id, status}`.
struct GeneratePlanResponse: Codable, Equatable {
    var jobId: String
    var status: JobStatus?

    init(jobId: String, status: JobStatus? = nil) {
        self.jobId = jobId
        self.status = status
    }
}

/// `objective:{primary, weights:{}}` — the solver's primary objective plus its weighting map.
struct PlanObjective: Codable, Equatable {
    var primary: String
    var weights: [String: Double]

    init(primary: String, weights: [String: Double] = [:]) {
        self.primary = primary
        self.weights = weights
    }

    static let balanced = PlanObjective(
        primary: "minimize_travel_time",
        weights: ["travel_time": 0.6, "customer_priority": 0.25, "compactness": 0.15]
    )

    var weightsSummary: String {
        guard !weights.isEmpty else { return primary }
        let rendered = weights.keys.sorted().map { "\($0)=\(String(format: "%.2f", weights[$0] ?? 0))" }
        return "\(primary) · " + rendered.joined(separator: ", ")
    }
}

/// `POST /v1/trips/{id}/plans:generate` request body.
/// Every field is optional: the backend applies its own defaults when the client sends `{}`.
struct GeneratePlanRequest: Codable, Equatable {
    var solverBudgetSeconds: Int?
    var objective: PlanObjective?

    init(solverBudgetSeconds: Int? = nil, objective: PlanObjective? = nil) {
        self.solverBudgetSeconds = solverBudgetSeconds
        self.objective = objective
    }

    static let defaultBudgetSeconds = 30

    static let `default` = GeneratePlanRequest(
        solverBudgetSeconds: defaultBudgetSeconds,
        objective: .balanced
    )
}
