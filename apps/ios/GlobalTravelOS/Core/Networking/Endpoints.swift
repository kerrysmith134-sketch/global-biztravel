import Foundation

/// Declarative catalog of the `/v1` surface. Paths and field names mirror the backend contract
/// one-for-one; nothing here invents a route.
enum Endpoints {

    // MARK: - Health

    static func health() -> APIRequest {
        APIRequest(method: .get, path: "/v1/health")
    }

    // MARK: - Trips

    static func createTrip(_ body: CreateTripRequest) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    /// Backend-shaped create body (`window_start` / `origin_place_id`). The domain request keeps
    /// the nested origin/window; the repository creates the origin place first and calls this.
    static func createTrip(_ body: BackendTripCreate) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func createPlace(_ body: BackendPlaceCreate) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/places",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func listTrips(page: PageRequest) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips", queryItems: page.queryItems)
    }

    static func tripDetail(id: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips/\(id)")
    }

    static func holdTrip(id: String, version: Int) -> APIRequest {
        APIRequest(method: .post, path: "/v1/trips/\(id)/hold", ifMatch: String(version))
    }

    static func resumeTrip(id: String, version: Int) -> APIRequest {
        APIRequest(method: .post, path: "/v1/trips/\(id)/resume", ifMatch: String(version))
    }

    static func completeTrip(id: String, version: Int) -> APIRequest {
        APIRequest(method: .post, path: "/v1/trips/\(id)/complete", ifMatch: String(version))
    }

    // MARK: - Imports

    static func parseImport(
        tripId: String,
        fileData: Data,
        filename: String,
        mimeType: String = "text/csv"
    ) -> APIRequest {
        let boundary = "Boundary-\(UUID().uuidString)"
        var body = Data()
        body.append(utf8("--\(boundary)\r\n"))
        body.append(utf8("Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n"))
        body.append(utf8("Content-Type: \(mimeType)\r\n\r\n"))
        body.append(fileData)
        body.append(utf8("\r\n--\(boundary)--\r\n"))
        return APIRequest(
            method: .post,
            path: "/v1/trips/\(tripId)/imports:parse",
            body: body,
            contentType: "multipart/form-data; boundary=\(boundary)",
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func confirmImport(tripId: String, body: ConfirmImportRequest) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips/\(tripId)/imports:confirm",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    // MARK: - Planning

    static func generatePlan(tripId: String, body: GeneratePlanRequest) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips/\(tripId)/plans:generate",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func createPlanChange(tripId: String, body: PlanChangeCreate) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips/\(tripId)/plan-changes",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func job(id: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/jobs/\(id)")
    }

    static func listPlans(tripId: String, page: PageRequest) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips/\(tripId)/plans", queryItems: page.queryItems)
    }

    static func planDetail(tripId: String, planId: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips/\(tripId)/plans/\(planId)")
    }

    static func planDiff(tripId: String, planId: String, against: String) -> APIRequest {
        APIRequest(
            method: .get,
            path: "/v1/trips/\(tripId)/plans/\(planId):diff",
            queryItems: [URLQueryItem(name: "against", value: against)]
        )
    }

    static func activatePlan(tripId: String, planId: String, tripVersion: Int) -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips/\(tripId)/plans/\(planId):activate",
            idempotencyKey: APIRequest.newIdempotencyKey(),
            ifMatch: String(tripVersion)
        )
    }

    // MARK: - Visits

    static func submitVisitRecord(visitId: String, body: VisitRecordRequest) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/visits/\(visitId)/records",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func listVisits(tripId: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips/\(tripId)/visits")
    }

    static func createVisit(tripId: String, body: [String: Any]) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips/\(tripId)/visits",
            body: try JSONSerialization.data(withJSONObject: body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    // MARK: - Customers（审计 5.5：客户端客户管理）

    static func listCustomers(query: String?, page: PageRequest) -> APIRequest {
        var items: [URLQueryItem] = [URLQueryItem(name: "limit", value: String(page.limit))]
        if let query, !query.isEmpty {
            items.append(URLQueryItem(name: "query", value: query))
        }
        if let cursor = page.cursor {
            items.append(URLQueryItem(name: "cursor", value: cursor))
        }
        return APIRequest(method: .get, path: "/v1/customers", queryItems: items)
    }

    /// Organization-level place catalog for attaching a location to one or more customers.
    static func listPlaces(query: String?, countryCode: String?, page: PageRequest) -> APIRequest {
        var items: [URLQueryItem] = [URLQueryItem(name: "limit", value: String(page.limit))]
        if let query, !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            items.append(URLQueryItem(name: "query", value: query))
        }
        if let countryCode, !countryCode.isEmpty {
            items.append(URLQueryItem(name: "country_code", value: countryCode.uppercased()))
        }
        if let cursor = page.cursor {
            items.append(URLQueryItem(name: "cursor", value: cursor))
        }
        return APIRequest(method: .get, path: "/v1/places", queryItems: items)
    }

    static func createPlace(_ body: PlaceCreateRequest) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/places",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func customerMapLocations(filters: GlobalCustomerMapFilters, page: PageRequest) -> APIRequest {
        var items = [URLQueryItem(name: "limit", value: String(page.limit))]
        if let query = filters.query, !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            items.append(URLQueryItem(name: "query", value: query))
        }
        if let tier = filters.tier, !tier.isEmpty {
            items.append(URLQueryItem(name: "tier", value: tier.uppercased()))
        }
        if let country = filters.countryCode, !country.isEmpty {
            items.append(URLQueryItem(name: "country_code", value: country.uppercased()))
        }
        if let quality = filters.geocodeStatus, !quality.isEmpty {
            items.append(URLQueryItem(name: "geocode_status", value: quality.uppercased()))
        }
        if let cursor = page.cursor { items.append(URLQueryItem(name: "cursor", value: cursor)) }
        return APIRequest(method: .get, path: "/v1/customers/map-locations", queryItems: items)
    }

    static func createCustomer(_ body: CustomerCreateRequest) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/customers",
            body: try JSONEncoder().encode(body)
        )
    }

    static func customerDetail(id: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/customers/\(id)")
    }

    /// All explicitly linked addresses for a customer.
    static func customerPlaces(customerId: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/customers/\(customerId)/places")
    }

    static func linkCustomerPlace(
        customerId: String,
        request: CustomerPlaceLinkRequest
    ) throws -> APIRequest {
        var payload: [String: Any] = [
            "place_id": request.placeId,
            "is_primary": request.isPrimary,
        ]
        if let label = request.label { payload["label"] = label }
        return APIRequest(
            method: .post,
            path: "/v1/customers/\(customerId)/places",
            body: try JSONSerialization.data(withJSONObject: payload),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func unlinkCustomerPlace(customerId: String, placeId: String) -> APIRequest {
        APIRequest(
            method: .delete,
            path: "/v1/customers/\(customerId)/places/\(placeId)"
        )
    }

    // MARK: - Assistant

    static func createAssistantConversation(body: [String: String]) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/assistant/conversations",
            body: try JSONSerialization.data(withJSONObject: body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func listAssistantConversations() -> APIRequest {
        APIRequest(method: .get, path: "/v1/assistant/conversations")
    }

    static func assistantConversation(id: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/assistant/conversations/\(id)")
    }

    static func deleteAssistantConversation(id: String) -> APIRequest {
        APIRequest(method: .delete, path: "/v1/assistant/conversations/\(id)")
    }

    static func postAssistantMessage(id: String, content: String) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/assistant/conversations/\(id)/messages",
            body: try DateCoding.encode(AssistantMessageCreate(content: content)),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    // MARK: - User-intent research

    static func createResearchPlan(_ body: ResearchPlanCreateRequest) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/research/plans",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func researchPlan(id: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/research/plans/\(id)")
    }

    static func executeResearchPlan(
        id: String,
        body: ResearchExecutionRequest
    ) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/research/plans/\(id):execute",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

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

    // MARK: - Traveler profile

    static func travelerProfile() -> APIRequest {
        APIRequest(method: .get, path: "/v1/traveler/profile")
    }

    static func updateTravelerProfile(_ body: TravelerProfileUpdate) throws -> APIRequest {
        APIRequest(
            method: .put,
            path: "/v1/traveler/profile",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    // MARK: - Trip outcome

    static func tripOutcome(tripId: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips/\(tripId)/outcome")
    }

    // MARK: - Local guide

    static func localGuide(tripId: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips/\(tripId)/local-guide")
    }

    // MARK: - Notifications

    static func notifications(unreadOnly: Bool = false, priority: String? = nil) -> APIRequest {
        var items: [URLQueryItem] = []
        if unreadOnly { items.append(URLQueryItem(name: "unread_only", value: "true")) }
        if let priority { items.append(URLQueryItem(name: "priority", value: priority)) }
        return APIRequest(method: .get, path: "/v1/notifications", queryItems: items)
    }

    static func markNotificationRead(id: String) -> APIRequest {
        APIRequest(method: .post, path: "/v1/notifications/\(id):read")
    }

    static func assistantAction(conversationId: String, action: String, subjectId: String) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/assistant/conversations/\(conversationId)/actions",
            body: try JSONSerialization.data(
                withJSONObject: ["action": action, "subject_id": subjectId]
            ),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func notificationPreferences() -> APIRequest {
        APIRequest(method: .get, path: "/v1/notifications/preferences")
    }

    static func updateNotificationPreferences(muted: [String]) throws -> APIRequest {
        APIRequest(
            method: .put,
            path: "/v1/notifications/preferences",
            body: try DateCoding.encode(NotificationPreferenceUpdate(mutedPriorities: muted)),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    // MARK: - Action Center

    static func actionCenter(tripId: String? = nil) -> APIRequest {
        APIRequest(
            method: .get,
            path: "/v1/action-center",
            queryItems: tripId.map { [URLQueryItem(name: "trip_id", value: $0)] } ?? []
        )
    }

    // MARK: - CRM

    static func crmSync(recordId: String) -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/visit-records/\(recordId)/crm-sync",
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func crmSyncJob(id: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/crm-sync-jobs/\(id)")
    }

    static func tripCrmSyncJobs(tripId: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips/\(tripId)/crm-sync-jobs")
    }

    static func visitBriefing(visitId: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/visits/\(visitId)/briefing")
    }

    static func updateVisitObjective(visitId: String, objective: String?) throws -> APIRequest {
        APIRequest(
            method: .patch,
            path: "/v1/visits/\(visitId)",
            body: try DateCoding.encode(VisitObjectiveUpdate(objective: objective)),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func draftMinutes(visitId: String) -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/visits/\(visitId)/minutes:draft",
            body: try? DateCoding.encode(MinutesDraftRequest()),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    // MARK: - Offline sync

    static func syncPush(body: SyncPushRequest) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/sync:push",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    // MARK: - Expenses

    static func tripExpenses(tripId: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips/\(tripId)/expenses")
    }

    static func tripBudget(tripId: String, baseCurrency: String? = nil) -> APIRequest {
        APIRequest(
            method: .get,
            path: "/v1/trips/\(tripId)/budget",
            queryItems: baseCurrency.map {
                [URLQueryItem(name: "base_currency", value: $0)]
            } ?? []
        )
    }

    static func createTripExpense(
        tripId: String,
        body: TripExpenseCreateRequest
    ) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips/\(tripId)/expenses",
            body: try DateCoding.encode(body),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func expenseReports(tripId: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips/\(tripId)/expense-reports")
    }

    static func createExpenseReport(tripId: String) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips/\(tripId)/expense-reports",
            body: try DateCoding.encode(ExpenseReportCreateRequest(notes: nil, expenseIds: nil)),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func submitExpenseReport(id: String) -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/expense-reports/\(id):submit",
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    // MARK: - Visa

    static func visaCases(tripId: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips/\(tripId)/visa-cases")
    }

    static func createVisaCases(tripId: String, countries: [String]?) throws -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips/\(tripId)/visa-cases",
            body: try DateCoding.encode(VisaCaseCreateRequest(countries: countries)),
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func updateVisaCase(id: String, status: VisaCaseStatus) throws -> APIRequest {
        APIRequest(
            method: .patch,
            path: "/v1/visa-cases/\(id)",
            body: try DateCoding.encode(VisaCaseUpdateRequest(status: status))
        )
    }

    static func entryAssessments(tripId: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips/\(tripId)/entry-assessments")
    }

    static func refreshEntryAssessments(tripId: String) -> APIRequest {
        APIRequest(
            method: .post,
            path: "/v1/trips/\(tripId)/entry-assessments:refresh"
        )
    }

    // MARK: - Proactive suggestions

    static func proactiveSuggestions(
        tripId: String?,
        status: String?,
        limit: Int,
        cursor: String?
    ) -> APIRequest {
        var items: [URLQueryItem] = []
        if let tripId, !tripId.isEmpty {
            items.append(URLQueryItem(name: "trip_id", value: tripId))
        }
        if let status, !status.isEmpty {
            items.append(URLQueryItem(name: "status", value: status))
        }
        items.append(URLQueryItem(name: "limit", value: String(limit)))
        if let cursor, !cursor.isEmpty {
            items.append(URLQueryItem(name: "cursor", value: cursor))
        }
        return APIRequest(method: .get, path: "/v1/proactive/suggestions", queryItems: items)
    }

    static func approveSuggestion(id: String) -> APIRequest {
        // No request body: the approval actor is taken from `X-User-Id`.
        APIRequest(
            method: .post,
            path: "/v1/proactive/suggestions/\(id):approve",
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    static func dismissSuggestion(id: String) -> APIRequest {
        // No request body: the contract does not define one for dismiss.
        APIRequest(
            method: .post,
            path: "/v1/proactive/suggestions/\(id):dismiss",
            idempotencyKey: APIRequest.newIdempotencyKey()
        )
    }

    // MARK: - Memory

    static func tripMemory(tripId: String) -> APIRequest {
        APIRequest(method: .get, path: "/v1/trips/\(tripId)/memory")
    }

    // MARK: - Helpers

    private static func utf8(_ string: String) -> Data {
        Data(string.utf8)
    }
}
