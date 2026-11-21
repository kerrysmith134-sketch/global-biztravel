import Foundation

protocol ResearchRepository {
    func createPlan(_ request: ResearchPlanCreateRequest) async throws -> ResearchPlan
    func executePlan(id: String) async throws -> ResearchExecutionAccepted
    func plan(id: String) async throws -> ResearchPlan
}

/// Research plans are authoritative server workflows. No local fallback can claim that
/// external sources were searched or verified.
final class RemoteResearchRepository: ResearchRepository {
    private let client: APIClient

    init(client: APIClient) {
        self.client = client
    }

    func createPlan(_ request: ResearchPlanCreateRequest) async throws -> ResearchPlan {
        try await client.send(try Endpoints.createResearchPlan(request), as: ResearchPlan.self)
    }

    func executePlan(id: String) async throws -> ResearchExecutionAccepted {
        try await client.send(Endpoints.executeResearchPlan(id: id), as: ResearchExecutionAccepted.self)
    }

    func plan(id: String) async throws -> ResearchPlan {
        try await client.send(Endpoints.researchPlan(id: id), as: ResearchPlan.self)
    }
}
