import Foundation

/// Risk cases are a server-owned projection over proactive suggestions and follow-ups.
/// They are intentionally not cached as ordinary offline data: stale lifecycle state could
/// mislead a traveler into believing a risk has been acknowledged or resolved.
protocol TripRiskRepository {
    func cases(tripId: String, status: String?, limit: Int) async throws -> Page<TripRiskCase>
    func events(tripId: String, caseId: String, limit: Int) async throws -> [TripRiskCaseEvent]
    func reconcile(tripId: String) async throws -> TripRiskReconciliation
    func transition(
        tripId: String,
        caseId: String,
        request: TripRiskTransitionRequest
    ) async throws -> TripRiskTransitionResponse
}

final class RemoteTripRiskRepository: TripRiskRepository {
    private let client: APIClient

    init(client: APIClient) {
        self.client = client
    }

    func cases(tripId: String, status: String?, limit: Int) async throws -> Page<TripRiskCase> {
        try await client.send(
            Endpoints.listTripRiskCases(tripId: tripId, status: status, limit: limit),
            as: Page<TripRiskCase>.self
        )
    }

    func events(tripId: String, caseId: String, limit: Int) async throws -> [TripRiskCaseEvent] {
        try await client.send(
            Endpoints.listTripRiskEvents(tripId: tripId, caseId: caseId, limit: limit),
            as: [TripRiskCaseEvent].self
        )
    }

    func reconcile(tripId: String) async throws -> TripRiskReconciliation {
        try await client.send(
            Endpoints.reconcileTripRiskCases(tripId: tripId),
            as: TripRiskReconciliation.self
        )
    }

    func transition(
        tripId: String,
        caseId: String,
        request: TripRiskTransitionRequest
    ) async throws -> TripRiskTransitionResponse {
        try await client.send(
            try Endpoints.transitionTripRiskCase(
                tripId: tripId,
                caseId: caseId,
                request: request
            ),
            as: TripRiskTransitionResponse.self
        )
    }
}

/// Local-first mode does not yet own the source tables that create server risk cases.
/// Fail explicitly rather than synthesizing or caching a misleading local state.
struct UnavailableLocalTripRiskRepository: TripRiskRepository {
    private func unavailable<T>() throws -> T {
        throw RepositoryError.requiresNetwork("风险与恢复视图目前由云端风险服务汇总，请切换到云端模式查看")
    }

    func cases(tripId: String, status: String?, limit: Int) async throws -> Page<TripRiskCase> {
        try unavailable()
    }

    func events(tripId: String, caseId: String, limit: Int) async throws -> [TripRiskCaseEvent] {
        try unavailable()
    }

    func reconcile(tripId: String) async throws -> TripRiskReconciliation {
        try unavailable()
    }

    func transition(
        tripId: String,
        caseId: String,
        request: TripRiskTransitionRequest
    ) async throws -> TripRiskTransitionResponse {
        try unavailable()
    }
}
