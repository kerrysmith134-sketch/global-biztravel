import Foundation

/// Result of a full generate-and-poll cycle: the terminal job plus, when the solver succeeded,
/// the produced plan version so the planning screen can render it without a second request.
struct PlanGenerationOutcome: Equatable {
    var job: PlanJob
    var plan: PlanVersionDetail?

    var succeeded: Bool { job.resolvedStatus.isSuccess }

    var planVersionId: String? { plan?.id ?? job.producedPlanVersionId }

    var message: String {
        if succeeded {
            guard let plan else { return "计划已生成（\(job.producedPlanVersionId ?? "未知版本")）" }
            return "计划 \(plan.versionLabel) 已生成：\(plan.displayDayCount) 天 / \(plan.displayStopCount) 停靠"
        }
        if let error = job.displayError { return "计划生成失败：\(error)" }
        return "计划生成未完成（\(job.statusLabel)）"
    }
}

/// Drives `POST /v1/trips/{id}/plans:generate` (202 + `job_id`) and then polls
/// `GET /v1/jobs/{job_id}` until the job reaches a terminal status.
///
/// The sleep function is injectable so tests can exercise the polling loop — including the
/// timeout path — without waiting in real time.
final class GeneratePlanUseCase {
    struct PollingPolicy: Equatable {
        var interval: TimeInterval
        var maxAttempts: Int
        /// Stop early instead of polling forever when the job never leaves PENDING.
        var timeout: TimeInterval

        init(interval: TimeInterval = 2, maxAttempts: Int = 30, timeout: TimeInterval = 120) {
            self.interval = interval
            self.maxAttempts = maxAttempts
            self.timeout = timeout
        }

        static let `default` = PollingPolicy()
        static let fast = PollingPolicy(interval: 0.5, maxAttempts: 10, timeout: 10)
    }

    private let repository: PlanRepository
    private let policy: PollingPolicy
    private let sleeper: (TimeInterval) async throws -> Void
    private let clock: () -> Date

    init(
        repository: PlanRepository,
        policy: PollingPolicy = .default,
        sleeper: ((TimeInterval) async throws -> Void)? = nil,
        clock: @escaping () -> Date = { Date() }
    ) {
        self.repository = repository
        self.policy = policy
        self.sleeper = sleeper ?? { seconds in
            let nanoseconds = UInt64(max(seconds, 0) * 1_000_000_000)
            try await Task.sleep(nanoseconds: nanoseconds)
        }
        self.clock = clock
    }

    /// Fires the solve and waits for it. `onProgress` receives every observed job so the UI can
    /// show “求解中 · 尝试 n 次” while it waits.
    @discardableResult
    func generate(
        tripId: String,
        request: GeneratePlanRequest = .default,
        onProgress: ((PlanJob) -> Void)? = nil
    ) async throws -> PlanGenerationOutcome {
        let accepted = try await repository.generatePlan(tripId: tripId, request: request)
        let job = try await poll(jobId: accepted.jobId, onProgress: onProgress)

        guard job.resolvedStatus.isSuccess, let planId = job.producedPlanVersionId else {
            return PlanGenerationOutcome(job: job, plan: nil)
        }
        let plan = try await repository.planDetail(tripId: tripId, planId: planId)
        return PlanGenerationOutcome(job: job, plan: plan)
    }

    /// Polls a job that was started earlier (e.g. the app was backgrounded mid-solve).
    func poll(jobId: String, onProgress: ((PlanJob) -> Void)? = nil) async throws -> PlanJob {
        let startedAt = clock()
        var last = PlanJob(id: jobId, status: .pending)

        for attempt in 1 ... max(policy.maxAttempts, 1) {
            let job = try await repository.job(id: jobId)
            last = job
            onProgress?(job)

            if job.resolvedStatus.isTerminal {
                return job
            }
            if clock().timeIntervalSince(startedAt) >= policy.timeout {
                return job
            }
            if attempt < policy.maxAttempts {
                try await sleeper(policy.interval)
            }
        }
        return last
    }
}
