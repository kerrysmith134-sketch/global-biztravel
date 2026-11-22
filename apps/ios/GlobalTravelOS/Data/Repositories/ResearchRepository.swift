import Foundation

protocol ResearchRepository {
    func createPlan(_ request: ResearchPlanCreateRequest) async throws -> ResearchPlan
    func executePlan(id: String, searchQueries: [String]) async throws -> ResearchExecutionAccepted
    func plan(id: String) async throws -> ResearchPlan
    func listWorkspaces(page: PageRequest) async throws -> Page<MarketWorkspace>
    func createWorkspace(_ request: MarketWorkspaceCreateRequest) async throws -> MarketWorkspace
    func workspace(id: String) async throws -> MarketWorkspace
    func listContexts(workspaceId: String) async throws -> [MarketContextSnapshotRead]
    func extractContext(workspaceId: String, contextId: String) async throws -> MarketContextSnapshotRead
    func evidenceIntelligence(workspaceId: String) async throws -> MarketEvidenceIntelligenceReport
    func sourceVerificationPlan(workspaceId: String) async throws -> MarketSourceVerificationPlan
    func applySourceVerificationPlan(workspaceId: String) async throws -> MarketSourceVerificationPlanApplication
    func claimHistory(workspaceId: String, claimId: String) async throws -> MarketClaimLineageHistory
    func listQuestions(workspaceId: String, page: PageRequest) async throws -> Page<MarketResearchQuestion>
    func createQuestion(
        workspaceId: String,
        request: MarketResearchQuestionCreateRequest
    ) async throws -> MarketResearchQuestion
    func graph(workspaceId: String) async throws -> MarketResearchGraph
    func startExecution(
        workspaceId: String,
        request: MarketResearchGraphExecutionCreate
    ) async throws -> MarketResearchGraphExecutionAccepted
    func execution(workspaceId: String, executionId: String) async throws -> MarketResearchGraphExecution
    func cancelExecution(workspaceId: String, executionId: String) async throws
    func listWorkspacePlans(workspaceId: String, targetQuestionId: String?) async throws -> [ResearchPlan]
    func draftAnswer(
        workspaceId: String,
        questionId: String
    ) async throws -> MarketQuestionAnswerGenerationAccepted
    func answers(workspaceId: String, questionId: String) async throws -> [MarketQuestionAnswer]
    func acceptAnswer(
        workspaceId: String,
        questionId: String,
        answerId: String,
        reviewNote: String?
    ) async throws -> MarketQuestionAnswer
    func rejectAnswer(
        workspaceId: String,
        questionId: String,
        answerId: String,
        reviewNote: String?
    ) async throws -> MarketQuestionAnswer
}

extension ResearchRepository {
    func listContexts(workspaceId: String) async throws -> [MarketContextSnapshotRead] {
        throw RepositoryError.requiresNetwork("当前研究仓库未实现上下文版本读取")
    }

    func extractContext(
        workspaceId: String,
        contextId: String
    ) async throws -> MarketContextSnapshotRead {
        throw RepositoryError.requiresNetwork("当前研究仓库未实现上下文提取")
    }

    func evidenceIntelligence(workspaceId: String) async throws -> MarketEvidenceIntelligenceReport {
        throw RepositoryError.requiresNetwork("当前研究仓库未实现证据质量分析")
    }

    func sourceVerificationPlan(workspaceId: String) async throws -> MarketSourceVerificationPlan {
        throw RepositoryError.requiresNetwork("当前研究仓库未实现来源核验计划")
    }

    func applySourceVerificationPlan(workspaceId: String) async throws -> MarketSourceVerificationPlanApplication {
        throw RepositoryError.requiresNetwork("当前研究仓库未实现核验任务同步")
    }

    func claimHistory(
        workspaceId: String,
        claimId: String
    ) async throws -> MarketClaimLineageHistory {
        throw RepositoryError.requiresNetwork("当前研究仓库未实现主张版本历史")
    }
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

    func executePlan(id: String, searchQueries: [String]) async throws -> ResearchExecutionAccepted {
        try await client.send(
            try Endpoints.executeResearchPlan(
                id: id,
                body: ResearchExecutionRequest(searchQueries: searchQueries)
            ),
            as: ResearchExecutionAccepted.self
        )
    }

    func plan(id: String) async throws -> ResearchPlan {
        try await client.send(Endpoints.researchPlan(id: id), as: ResearchPlan.self)
    }

    func listWorkspaces(page: PageRequest) async throws -> Page<MarketWorkspace> {
        try await client.send(Endpoints.listMarketWorkspaces(page: page), as: Page<MarketWorkspace>.self)
    }

    func createWorkspace(_ request: MarketWorkspaceCreateRequest) async throws -> MarketWorkspace {
        try await client.send(
            try Endpoints.createMarketWorkspace(request),
            as: MarketWorkspace.self
        )
    }

    func workspace(id: String) async throws -> MarketWorkspace {
        try await client.send(Endpoints.marketWorkspace(id: id), as: MarketWorkspace.self)
    }

    func listContexts(workspaceId: String) async throws -> [MarketContextSnapshotRead] {
        try await client.send(
            Endpoints.listMarketContexts(workspaceId: workspaceId),
            as: [MarketContextSnapshotRead].self
        )
    }

    func extractContext(
        workspaceId: String,
        contextId: String
    ) async throws -> MarketContextSnapshotRead {
        try await client.send(
            Endpoints.extractMarketContext(workspaceId: workspaceId, contextId: contextId),
            as: MarketContextSnapshotRead.self
        )
    }

    func evidenceIntelligence(workspaceId: String) async throws -> MarketEvidenceIntelligenceReport {
        try await client.send(
            Endpoints.marketEvidenceIntelligence(workspaceId: workspaceId),
            as: MarketEvidenceIntelligenceReport.self
        )
    }

    func sourceVerificationPlan(workspaceId: String) async throws -> MarketSourceVerificationPlan {
        try await client.send(
            Endpoints.marketSourceVerificationPlan(workspaceId: workspaceId),
            as: MarketSourceVerificationPlan.self
        )
    }

    func applySourceVerificationPlan(workspaceId: String) async throws -> MarketSourceVerificationPlanApplication {
        try await client.send(
            Endpoints.applyMarketSourceVerificationPlan(workspaceId: workspaceId),
            as: MarketSourceVerificationPlanApplication.self
        )
    }

    func claimHistory(
        workspaceId: String,
        claimId: String
    ) async throws -> MarketClaimLineageHistory {
        try await client.send(
            Endpoints.marketClaimHistory(workspaceId: workspaceId, claimId: claimId),
            as: MarketClaimLineageHistory.self
        )
    }

    func listQuestions(
        workspaceId: String,
        page: PageRequest
    ) async throws -> Page<MarketResearchQuestion> {
        try await client.send(
            Endpoints.listMarketQuestions(workspaceId: workspaceId, page: page),
            as: Page<MarketResearchQuestion>.self
        )
    }

    func createQuestion(
        workspaceId: String,
        request: MarketResearchQuestionCreateRequest
    ) async throws -> MarketResearchQuestion {
        try await client.send(
            try Endpoints.createMarketQuestion(workspaceId: workspaceId, body: request),
            as: MarketResearchQuestion.self
        )
    }

    func graph(workspaceId: String) async throws -> MarketResearchGraph {
        try await client.send(
            Endpoints.marketResearchGraph(workspaceId: workspaceId),
            as: MarketResearchGraph.self
        )
    }

    func startExecution(
        workspaceId: String,
        request: MarketResearchGraphExecutionCreate
    ) async throws -> MarketResearchGraphExecutionAccepted {
        try await client.send(
            try Endpoints.startMarketResearchGraphExecution(workspaceId: workspaceId, body: request),
            as: MarketResearchGraphExecutionAccepted.self
        )
    }

    func execution(workspaceId: String, executionId: String) async throws -> MarketResearchGraphExecution {
        try await client.send(
            Endpoints.marketResearchGraphExecution(workspaceId: workspaceId, executionId: executionId),
            as: MarketResearchGraphExecution.self
        )
    }

    func cancelExecution(workspaceId: String, executionId: String) async throws {
        try await client.sendWithoutResponse(
            Endpoints.cancelMarketResearchGraphExecution(workspaceId: workspaceId, executionId: executionId)
        )
    }

    func listWorkspacePlans(
        workspaceId: String,
        targetQuestionId: String?
    ) async throws -> [ResearchPlan] {
        try await client.send(
            Endpoints.listMarketResearchPlans(
                workspaceId: workspaceId,
                targetQuestionId: targetQuestionId
            ),
            as: [ResearchPlan].self
        )
    }

    func draftAnswer(
        workspaceId: String,
        questionId: String
    ) async throws -> MarketQuestionAnswerGenerationAccepted {
        try await client.send(
            try Endpoints.draftMarketQuestionAnswer(
                workspaceId: workspaceId,
                questionId: questionId,
                body: MarketQuestionAnswerGenerationCreate()
            ),
            as: MarketQuestionAnswerGenerationAccepted.self
        )
    }

    func answers(workspaceId: String, questionId: String) async throws -> [MarketQuestionAnswer] {
        try await client.send(
            Endpoints.listMarketQuestionAnswers(workspaceId: workspaceId, questionId: questionId),
            as: [MarketQuestionAnswer].self
        )
    }

    func acceptAnswer(
        workspaceId: String,
        questionId: String,
        answerId: String,
        reviewNote: String?
    ) async throws -> MarketQuestionAnswer {
        try await client.send(
            try Endpoints.acceptMarketQuestionAnswer(
                workspaceId: workspaceId,
                questionId: questionId,
                answerId: answerId,
                body: MarketQuestionAnswerReviewRequest(reviewNote: reviewNote)
            ),
            as: MarketQuestionAnswer.self
        )
    }

    func rejectAnswer(
        workspaceId: String,
        questionId: String,
        answerId: String,
        reviewNote: String?
    ) async throws -> MarketQuestionAnswer {
        try await client.send(
            try Endpoints.rejectMarketQuestionAnswer(
                workspaceId: workspaceId,
                questionId: questionId,
                answerId: answerId,
                body: MarketQuestionAnswerReviewRequest(reviewNote: reviewNote)
            ),
            as: MarketQuestionAnswer.self
        )
    }
}
