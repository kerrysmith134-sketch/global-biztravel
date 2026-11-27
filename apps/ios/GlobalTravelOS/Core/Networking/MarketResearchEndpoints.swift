import Foundation

extension Endpoints {
    // MARK: - Market research workspace

    static func listMarketWorkspaces(page: PageRequest) -> APIRequest {
        APIRequest(method: .get, path: "/v1/market/workspaces", queryItems: page.queryItems)
    }

    static func createMarketWorkspace(_ body: MarketWorkspaceCreateRequest) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/market/workspaces",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func marketWorkspace(id: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/market/workspaces/\(id)")
    }

    static func marketEvidenceIntelligence(workspaceId: String) -> APIRequest {
        APIRequest(
            method: .get,
            path: "/v1/market/workspaces/\(workspaceId)/evidence-intelligence"
        )
    }

    static func marketSourceVerificationPlan(workspaceId: String) -> APIRequest {
        APIRequest(
            method: .get,
            path: "/v1/market/workspaces/\(workspaceId)/source-verification-plan"
        )
    }

    static func applyMarketSourceVerificationPlan(workspaceId: String) -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/market/workspaces/\(workspaceId)/source-verification-plan:apply",
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func marketClaimHistory(workspaceId: String, claimId: String) -> APIRequest {
        APIRequest(
            method: .get,
            path: "/v1/market/workspaces/\(workspaceId)/claims/\(claimId)/history"
        )
    }

    static func marketClaimImpactReport(workspaceId: String) -> APIRequest {
        APIRequest(
            method: .get,
            path: "/v1/market/workspaces/\(workspaceId)/claim-impact-report"
        )
    }

    static func listMarketInsights(workspaceId: String, page: PageRequest) -> APIRequest {
        APIRequest(
            method: .get,
            path: "/v1/market/workspaces/\(workspaceId)/insights",
            queryItems: page.queryItems
        )
    }

    static func marketOpportunityDetail(workspaceId: String, opportunityId: String) -> APIRequest {
        APIRequest(
            method: .get,
            path: "/v1/market/workspaces/\(workspaceId)/opportunities/\(opportunityId)"
        )
    }

    static func marketOpportunityExecution(
        workspaceId: String,
        opportunityId: String
    ) -> APIRequest {
        APIRequest(
            method: .get,
            path: "/v1/market/workspaces/\(workspaceId)/opportunities/\(opportunityId)/execution"
        )
    }

    static func linkMarketOpportunityVisit(
        workspaceId: String,
        opportunityId: String,
        body: MarketOpportunityVisitLinkCreate
    ) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/market/workspaces/\(workspaceId)/opportunities/\(opportunityId)/visits",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func unlinkMarketOpportunityVisit(
        workspaceId: String,
        opportunityId: String,
        visitId: String,
        body: MarketOpportunityVisitUnlinkRequest
    ) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/market/workspaces/\(workspaceId)/opportunities/\(opportunityId)/visits/\(visitId):unlink",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func acceptMarketInsight(
        workspaceId: String,
        insightId: String,
        body: MarketQuestionAnswerReviewRequest
    ) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/market/workspaces/\(workspaceId)/insights/\(insightId):accept",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func rejectMarketInsight(
        workspaceId: String,
        insightId: String,
        body: MarketQuestionAnswerReviewRequest
    ) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/market/workspaces/\(workspaceId)/insights/\(insightId):reject",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func listMarketContexts(workspaceId: String) -> APIRequest {
        APIRequest(
            method: .get,
            path: "/v1/market/workspaces/\(workspaceId)/contexts"
        )
    }

    static func extractMarketContext(workspaceId: String, contextId: String) -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/market/workspaces/\(workspaceId)/contexts/\(contextId):extract",
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }


    static func listMarketQuestions(workspaceId: String, page: PageRequest) -> APIRequest {
        APIRequest(
            method: .get,
            path: "/v1/market/workspaces/\(workspaceId)/questions",
            queryItems: page.queryItems
        )
    }

    static func createMarketQuestion(
        workspaceId: String,
        body: MarketResearchQuestionCreateRequest
    ) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/market/workspaces/\(workspaceId)/questions",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func marketResearchGraph(workspaceId: String) -> APIRequest {
        APIRequest(
            method: .get,
            path: "/v1/market/workspaces/\(workspaceId)/research-graph"
        )
    }

    static func startMarketResearchGraphExecution(
        workspaceId: String,
        body: MarketResearchGraphExecutionCreate
    ) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/market/workspaces/\(workspaceId)/research-graph/executions",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func marketResearchGraphExecution(
        workspaceId: String,
        executionId: String
    ) -> APIRequest {
        APIRequest(
            method: .get,
            path: "/v1/market/workspaces/\(workspaceId)/research-graph/executions/\(executionId)"
        )
    }

    static func cancelMarketResearchGraphExecution(
        workspaceId: String,
        executionId: String
    ) -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/market/workspaces/\(workspaceId)/research-graph/executions/\(executionId):cancel",
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func listMarketResearchPlans(
        workspaceId: String,
        targetQuestionId: String?
    ) -> APIRequest {
        var queryItems: [URLQueryItem] = []
        if let targetQuestionId {
            queryItems.append(URLQueryItem(name: "target_question_id", value: targetQuestionId))
        }
        return APIRequest(
            method: .get,
            path: "/v1/market/workspaces/\(workspaceId)/research-plans",
            queryItems: queryItems
        )
    }

    static func draftMarketQuestionAnswer(
        workspaceId: String,
        questionId: String,
        body: MarketQuestionAnswerGenerationCreate
    ) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/market/workspaces/\(workspaceId)/questions/\(questionId):draft-answer",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func listMarketQuestionAnswers(
        workspaceId: String,
        questionId: String
    ) -> APIRequest {
        APIRequest(
            method: .get,
            path: "/v1/market/workspaces/\(workspaceId)/questions/\(questionId)/answers"
        )
    }

    static func acceptMarketQuestionAnswer(
        workspaceId: String,
        questionId: String,
        answerId: String,
        body: MarketQuestionAnswerReviewRequest
    ) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/market/workspaces/\(workspaceId)/questions/\(questionId)/answers/\(answerId):accept",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func rejectMarketQuestionAnswer(
        workspaceId: String,
        questionId: String,
        answerId: String,
        body: MarketQuestionAnswerReviewRequest
    ) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/market/workspaces/\(workspaceId)/questions/\(questionId)/answers/\(answerId):reject",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }
}
