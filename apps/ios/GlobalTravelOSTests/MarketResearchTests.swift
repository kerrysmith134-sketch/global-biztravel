import XCTest
@testable import GlobalTravelOS

final class MarketResearchTests: XCTestCase {
    func testResearchPlanRequestEncodesTargetQuestionID() throws {
        let request = ResearchPlanCreateRequest(
            request: "核验当前问题的公开证据",
            sourceScope: .externalOnly,
            workspaceId: "workspace-1",
            targetQuestionId: "question-1"
        )
        let data = try DateCoding.encode(request)
        let payload = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])

        XCTAssertEqual(payload["workspace_id"] as? String, "workspace-1")
        XCTAssertEqual(payload["target_question_id"] as? String, "question-1")
        XCTAssertEqual(payload["source_scope"] as? String, "EXTERNAL_ONLY")
    }

    func testMarketWorkspaceCreateEndpointUsesIdempotencyAndSnakeCase() throws {
        let request = try Endpoints.createMarketWorkspace(
            MarketWorkspaceCreateRequest(
                name: "East Africa research",
                objective: "Map the relevant market",
                initialContext: "User supplied background"
            )
        )
        XCTAssertEqual(request.method, .post)
        XCTAssertEqual(request.path, "/v1/market/workspaces")
        XCTAssertNotNil(request.idempotencyKey)

        let data = try XCTUnwrap(request.body)
        let payload = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(payload["initial_context"] as? String, "User supplied background")
    }

    func testEvidenceIntelligenceEndpointUsesReadOnlyWorkspaceRoute() {
        let request = Endpoints.marketEvidenceIntelligence(workspaceId: "workspace-1")
        XCTAssertEqual(request.method, .get)
        XCTAssertEqual(
            request.path,
            "/v1/market/workspaces/workspace-1/evidence-intelligence"
        )
        XCTAssertNil(request.idempotencyKey)
        XCTAssertNil(request.body)
    }

    func testMarketContextEndpointsUseVersionedWorkspaceRoutes() {
        let list = Endpoints.listMarketContexts(workspaceId: "workspace-1")
        XCTAssertEqual(list.method, .get)
        XCTAssertEqual(list.path, "/v1/market/workspaces/workspace-1/contexts")

        let extract = Endpoints.extractMarketContext(
            workspaceId: "workspace-1",
            contextId: "context-1"
        )
        XCTAssertEqual(extract.method, .post)
        XCTAssertEqual(
            extract.path,
            "/v1/market/workspaces/workspace-1/contexts/context-1:extract"
        )
        XCTAssertNotNil(extract.idempotencyKey)
        XCTAssertNil(extract.body)
    }

    func testResearchWorkspaceAndQuestionEndpointsUseServerRoutes() throws {
        let list = Endpoints.listMarketWorkspaces(page: PageRequest(limit: 25, cursor: "next"))
        XCTAssertEqual(list.method, .get)
        XCTAssertEqual(list.path, "/v1/market/workspaces")
        XCTAssertEqual(list.queryItems.map(\.name), ["limit", "cursor"])

        let create = try Endpoints.createMarketQuestion(
            workspaceId: "workspace-1",
            body: MarketResearchQuestionCreateRequest(
                question: "核验目标市场的监管框架",
                rationale: "决定后续市场准入研究",
                priority: 1,
                acceptanceCriteria: ["来自官方监管来源"]
            )
        )
        XCTAssertEqual(create.method, .post)
        XCTAssertEqual(create.path, "/v1/market/workspaces/workspace-1/questions")
        XCTAssertNotNil(create.idempotencyKey)

        let graph = Endpoints.marketResearchGraph(workspaceId: "workspace-1")
        XCTAssertEqual(graph.path, "/v1/market/workspaces/workspace-1/research-graph")
    }

    func testResearchExecutionAndHumanReviewEndpointsAreIdempotent() throws {
        let start = try Endpoints.startMarketResearchGraphExecution(
            workspaceId: "workspace-1",
            body: MarketResearchGraphExecutionCreate(
                maxConcurrency: 2,
                maxQuestions: 8,
                maxDurationMinutes: 45
            )
        )
        XCTAssertEqual(start.method, .post)
        XCTAssertEqual(
            start.path,
            "/v1/market/workspaces/workspace-1/research-graph/executions"
        )
        XCTAssertNotNil(start.idempotencyKey)

        let draft = try Endpoints.draftMarketQuestionAnswer(
            workspaceId: "workspace-1",
            questionId: "question-1",
            body: MarketQuestionAnswerGenerationCreate()
        )
        XCTAssertEqual(
            draft.path,
            "/v1/market/workspaces/workspace-1/questions/question-1:draft-answer"
        )
        XCTAssertNotNil(draft.idempotencyKey)

        let accept = try Endpoints.acceptMarketQuestionAnswer(
            workspaceId: "workspace-1",
            questionId: "question-1",
            answerId: "answer-1",
            body: MarketQuestionAnswerReviewRequest(reviewNote: "来源与结论一致")
        )
        XCTAssertEqual(
            accept.path,
            "/v1/market/workspaces/workspace-1/questions/question-1/answers/answer-1:accept"
        )
        XCTAssertNotNil(accept.idempotencyKey)

        let reject = try Endpoints.rejectMarketQuestionAnswer(
            workspaceId: "workspace-1",
            questionId: "question-1",
            answerId: "answer-1",
            body: MarketQuestionAnswerReviewRequest(reviewNote: "证据不足")
        )
        XCTAssertTrue(reject.path.hasSuffix("/answers/answer-1:reject"))
        XCTAssertNotNil(reject.idempotencyKey)
    }

    func testWorkspaceResearchPlanListCanFilterByTargetQuestion() {
        let request = Endpoints.listMarketResearchPlans(
            workspaceId: "workspace-1",
            targetQuestionId: "question-1"
        )
        XCTAssertEqual(request.method, .get)
        XCTAssertEqual(request.path, "/v1/market/workspaces/workspace-1/research-plans")
        XCTAssertEqual(request.queryItems.first?.name, "target_question_id")
        XCTAssertEqual(request.queryItems.first?.value, "question-1")
    }

    func testMarketResearchQuestionDefaultsOmittedEvidenceLists() throws {
        let json = """
        {
          "id": "question-1",
          "workspace_id": "workspace-1",
          "parent_question_id": null,
          "question": "核验目标市场监管框架",
          "rationale": "决定市场准入路径",
          "source": "USER",
          "priority": 1,
          "status": "OPEN",
          "answer_summary": null,
          "blocked_reason": null,
          "created_by_user_id": null,
          "created_at": "2026-11-22T08:00:00+00:00",
          "updated_at": "2026-11-22T08:00:00+00:00"
        }
        """
        let question = try DateCoding.decode(MarketResearchQuestion.self, fromJSON: json)

        XCTAssertEqual(question.id, "question-1")
        XCTAssertEqual(question.statusLabel, "待研究")
        XCTAssertTrue(question.acceptanceCriteria.isEmpty)
        XCTAssertTrue(question.expectedEvidence.isEmpty)
    }

    func testResearchGraphDecodesNodeReadinessAndDependencies() throws {
        let json = """
        {
          "workspace_id": "workspace-1",
          "nodes": [{
            "id": "question-1",
            "question": "核验市场监管框架",
            "rationale": "建立基础事实",
            "priority": 1,
            "status": "OPEN",
            "source": "USER",
            "parent_question_id": null,
            "generated_by_run_id": null,
            "planner_question_key": null,
            "acceptance_criteria": [],
            "expected_evidence": ["官方监管来源"],
            "depends_on_question_ids": [],
            "waiting_on_question_ids": [],
            "readiness": "WAITING_FOR_EVIDENCE"
          }],
          "edges": [],
          "topological_order": ["question-1"],
          "ready_question_ids": [],
          "cycle_detected": false
        }
        """
        let graph = try DateCoding.decode(MarketResearchGraph.self, fromJSON: json)

        XCTAssertEqual(graph.workspaceId, "workspace-1")
        XCTAssertFalse(graph.cycleDetected)
        XCTAssertEqual(graph.nodes.first?.readiness, "WAITING_FOR_EVIDENCE")
        XCTAssertEqual(graph.nodes.first?.expectedEvidence, ["官方监管来源"])
    }

    func testResearchExecutionDecodesDurableServerState() throws {
        let json = """
        {
          "execution_id": "execution-1",
          "workspace_id": "workspace-1",
          "workflow_status": "RUNNING",
          "execution_state": "WAITING_FOR_REVIEW",
          "graph_digest": "abc123",
          "max_concurrency": 2,
          "max_questions": 8,
          "max_duration_minutes": 45,
          "max_cost": null,
          "cost_estimate": null,
          "cost_metering_available": false,
          "dispatched_count": 1,
          "completed_count": 0,
          "active_count": 0,
          "ready_count": 0,
          "waiting_review_count": 1,
          "waiting_evidence_count": 0,
          "failed_count": 0,
          "node_states": [{
            "question_id": "question-1",
            "state": "WAITING_FOR_REVIEW",
            "latest_answer_id": "answer-1",
            "latest_answer_status": "NEEDS_REVIEW",
            "job_id": null,
            "error": null
          }],
          "stop_reason": null,
          "created_at": "2026-11-22T08:00:00+00:00",
          "started_at": "2026-11-22T08:01:00+00:00",
          "deadline_at": "2026-11-22T08:46:00+00:00",
          "finished_at": null
        }
        """
        let execution = try DateCoding.decode(
            MarketResearchGraphExecution.self,
            fromJSON: json
        )

        XCTAssertEqual(execution.id, "execution-1")
        XCTAssertEqual(execution.stateLabel, "等待人工审核")
        XCTAssertEqual(execution.nodeStates.first?.latestAnswerStatus, "NEEDS_REVIEW")
    }

    func testWorkspaceEvidenceSourceURLUsesPayloadURLNotVersionReference() throws {
        let json = """
        {
          "id": "evidence-1",
          "kind": "WEB_SEARCH_RESULT",
          "ref": "market-source:v1:immutable-version-hash",
          "source": "searxng",
          "payload": {
            "url": "https://example.com/regulation",
            "title": "Official regulation",
            "snippet": "A source snippet",
            "engine": "general",
            "published_at": "2026-11-20",
            "evidence_state": "UNVERIFIED_SEARCH_RESULT"
          },
          "retrieved_at": "2026-11-22T08:05:00+00:00",
          "created_at": "2026-11-22T08:05:00+00:00"
        }
        """
        let evidence = try DateCoding.decode(ResearchEvidence.self, fromJSON: json)

        XCTAssertEqual(evidence.ref, "market-source:v1:immutable-version-hash")
        XCTAssertEqual(evidence.sourceURL?.absoluteString, "https://example.com/regulation")
        XCTAssertTrue(evidence.isUnverifiedSearchResult)
    }
}
