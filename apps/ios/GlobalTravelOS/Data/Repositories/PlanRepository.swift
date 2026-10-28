import Foundation

/// Plan versions, plan details, diffs, generation and activation.
///
/// Activation is the reason this protocol exists in one piece: `activatePlan` throws the raw
/// `APIError` so `ActivatePlanUseCase` can recognise 409 / 412 / 422 by error code. The offline
/// wrapper below therefore must **not** translate activation failures into `RepositoryError`.
protocol PlanRepository {
    func listPlans(tripId: String, page: PageRequest) async throws -> Page<PlanVersion>
    func planDetail(tripId: String, planId: String) async throws -> PlanVersionDetail
    func planDiff(tripId: String, planId: String, against: String) async throws -> PlanDiff
    func activatePlan(tripId: String, planId: String, tripVersion: Int) async throws -> PlanActivation
    func generatePlan(tripId: String, request: GeneratePlanRequest) async throws -> GeneratePlanResponse
    func job(id: String) async throws -> PlanJob
}

/// Talks to `/v1/trips/{id}/plans*` and `/v1/jobs/{id}`.
final class RemotePlanRepository: PlanRepository {
    private let client: APIClient

    init(client: APIClient) {
        self.client = client
    }

    func listPlans(tripId: String, page: PageRequest) async throws -> Page<PlanVersion> {
        try await client.send(
            Endpoints.listPlans(tripId: tripId, page: page),
            as: Page<PlanVersion>.self
        )
    }

    func planDetail(tripId: String, planId: String) async throws -> PlanVersionDetail {
        try await client.send(
            Endpoints.planDetail(tripId: tripId, planId: planId),
            as: PlanVersionDetail.self
        )
    }

    func planDiff(tripId: String, planId: String, against: String) async throws -> PlanDiff {
        try await client.send(
            Endpoints.planDiff(tripId: tripId, planId: planId, against: against),
            as: PlanDiff.self
        )
    }

    func activatePlan(tripId: String, planId: String, tripVersion: Int) async throws -> PlanActivation {
        try await client.send(
            Endpoints.activatePlan(tripId: tripId, planId: planId, tripVersion: tripVersion),
            as: PlanActivation.self
        )
    }

    func generatePlan(tripId: String, request: GeneratePlanRequest) async throws -> GeneratePlanResponse {
        try await client.send(
            try Endpoints.generatePlan(tripId: tripId, body: request),
            as: GeneratePlanResponse.self
        )
    }

    func job(id: String) async throws -> PlanJob {
        try await client.send(Endpoints.job(id: id), as: PlanJob.self)
    }
}

/// Read-through wrapper.
///
/// Only the two genuinely cacheable, human-inspectable reads are wrapped: the version list and a
/// plan detail. Generation, job polling, diff and activation always go to the wire — a stale plan
/// detail is a survivable lie, but a stale *activation* verdict is not, and a diff against a
/// cached plan would be meaningless.
final class OfflinePlanRepository: PlanRepository {
    private let remote: PlanRepository
    private let listCache: JSONFileCache<[String: [PlanVersion]]>
    private let detailCache: JSONFileCache<[String: PlanVersionDetail]>
    private let indicator: OfflineIndicator

    init(
        remote: PlanRepository,
        listCache: JSONFileCache<[String: [PlanVersion]]>,
        detailCache: JSONFileCache<[String: PlanVersionDetail]>,
        indicator: OfflineIndicator
    ) {
        self.remote = remote
        self.listCache = listCache
        self.detailCache = detailCache
        self.indicator = indicator
    }

    convenience init(remote: PlanRepository, directory: URL, indicator: OfflineIndicator) {
        self.init(
            remote: remote,
            listCache: JSONFileCache<[String: [PlanVersion]]>(
                directory: directory,
                fileName: "plans-by-trip.json"
            ),
            detailCache: JSONFileCache<[String: PlanVersionDetail]>(
                directory: directory,
                fileName: "plan-details.json"
            ),
            indicator: indicator
        )
    }

    func listPlans(tripId: String, page: PageRequest) async throws -> Page<PlanVersion> {
        do {
            let result = try await remote.listPlans(tripId: tripId, page: page)
            if page.cursor == nil {
                var stored = listCache.load() ?? [:]
                stored[tripId] = result.items
                listCache.save(stored)
            }
            indicator.markServedFromServer()
            return result
        } catch {
            guard page.cursor == nil, let cached = listCache.load()?[tripId], !cached.isEmpty else {
                throw error
            }
            indicator.markServedFromCache("计划版本列表")
            return Page(items: cached, nextCursor: nil)
        }
    }

    func planDetail(tripId: String, planId: String) async throws -> PlanVersionDetail {
        do {
            let detail = try await remote.planDetail(tripId: tripId, planId: planId)
            var stored = detailCache.load() ?? [:]
            stored[planId] = detail
            detailCache.save(stored)
            indicator.markServedFromServer()
            return detail
        } catch {
            guard let cached = detailCache.load()?[planId] else { throw error }
            indicator.markServedFromCache("计划详情")
            return cached
        }
    }

    /// Always live: a diff is a statement about two server-side plans.
    func planDiff(tripId: String, planId: String, against: String) async throws -> PlanDiff {
        try await remote.planDiff(tripId: tripId, planId: planId, against: against)
    }

    /// Always live, and the `APIError` is re-thrown untouched so the 409/412/422 distinction
    /// survives into `ActivatePlanUseCase`.
    func activatePlan(tripId: String, planId: String, tripVersion: Int) async throws -> PlanActivation {
        try await remote.activatePlan(tripId: tripId, planId: planId, tripVersion: tripVersion)
    }

    /// Always live: the solver runs on the server, there is no offline stand-in.
    func generatePlan(tripId: String, request: GeneratePlanRequest) async throws -> GeneratePlanResponse {
        try await remote.generatePlan(tripId: tripId, request: request)
    }

    /// Always live: job status is progress, not data worth caching.
    func job(id: String) async throws -> PlanJob {
        try await remote.job(id: id)
    }
}