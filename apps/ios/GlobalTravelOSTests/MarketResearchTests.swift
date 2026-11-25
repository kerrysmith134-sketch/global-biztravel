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

    func testSourceVerificationPlanApplyEndpointIsIdempotentWrite() {
        let request = Endpoints.applyMarketSourceVerificationPlan(workspaceId: "workspace-1")
        XCTAssertEqual(request.method, .post)
        XCTAssertEqual(
            request.path,
            "/v1/market/workspaces/workspace-1/source-verification-plan:apply"
        )
        XCTAssertNotNil(request.idempotencyKey)
        XCTAssertNil(request.body)
    }

    func testSourceVerificationPlanEndpointIsReadOnly() {
        let request = Endpoints.marketSourceVerificationPlan(workspaceId: "workspace-1")
        XCTAssertEqual(request.method, .get)
        XCTAssertEqual(
            request.path,
            "/v1/market/workspaces/workspace-1/source-verification-plan"
        )
        XCTAssertNil(request.idempotencyKey)
        XCTAssertNil(request.body)
    }

    func testMarketClaimHistoryEndpointIsReadOnly() {
        let request = Endpoints.marketClaimHistory(
            workspaceId: "workspace-1",
            claimId: "claim-1"
        )
        XCTAssertEqual(request.method, .get)
        XCTAssertEqual(
            request.path,
            "/v1/market/workspaces/workspace-1/claims/claim-1/history"
        )
        XCTAssertNil(request.idempotencyKey)
        XCTAssertNil(request.body)
    }

    func testMarketClaimImpactReportEndpointIsReadOnly() {
        let request = Endpoints.marketClaimImpactReport(workspaceId: "workspace-1")
        XCTAssertEqual(request.method, .get)
        XCTAssertEqual(
            request.path,
            "/v1/market/workspaces/workspace-1/claim-impact-report"
        )
        XCTAssertNil(request.idempotencyKey)
        XCTAssertNil(request.body)
    }

    func testMarketImpactResolutionEndpointsUseExistingServerRoutes() throws {
        let list = Endpoints.listMarketInsights(
            workspaceId: "workspace-1",
            page: PageRequest(limit: 100, cursor: "next-page")
        )
        XCTAssertEqual(list.method, .get)
        XCTAssertEqual(list.path, "/v1/market/workspaces/workspace-1/insights")
        XCTAssertEqual(list.queryItems.map(\.name), ["limit", "cursor"])
        XCTAssertEqual(list.queryItems.map(\.value), ["100", "next-page"])
        XCTAssertNil(list.idempotencyKey)

        let opportunity = Endpoints.marketOpportunityDetail(
            workspaceId: "workspace-1",
            opportunityId: "opportunity-1"
        )
        XCTAssertEqual(opportunity.method, .get)
        XCTAssertEqual(
            opportunity.path,
            "/v1/market/workspaces/workspace-1/opportunities/opportunity-1"
        )

        let accept = try Endpoints.acceptMarketInsight(
            workspaceId: "workspace-1",
            insightId: "insight-1",
            body: MarketQuestionAnswerReviewRequest(reviewNote: "已核对最新证据")
        )
        XCTAssertEqual(accept.method, .post)
        XCTAssertEqual(
            accept.path,
            "/v1/market/workspaces/workspace-1/insights/insight-1:accept"
        )
        XCTAssertNotNil(accept.idempotencyKey)
        let acceptBody = try XCTUnwrap(accept.body)
        let acceptPayload = try XCTUnwrap(
            JSONSerialization.jsonObject(with: acceptBody) as? [String: Any]
        )
        XCTAssertEqual(acceptPayload["review_note"] as? String, "已核对最新证据")

        let reject = try Endpoints.rejectMarketInsight(
            workspaceId: "workspace-1",
            insightId: "insight-1",
            body: MarketQuestionAnswerReviewRequest(reviewNote: "新版本来源不再支持该判断")
        )
        XCTAssertEqual(reject.method, .post)
        XCTAssertEqual(
            reject.path,
            "/v1/market/workspaces/workspace-1/insights/insight-1:reject"
        )
        XCTAssertNotNil(reject.idempotencyKey)
    }

    func testMarketInsightDetailDecodesEvidenceAndReviewState() throws {
        let json = """
        {
          "id": "insight-1",
          "workspace_id": "workspace-1",
          "version": 2,
          "insight_key": "buyer-demand",
          "insight_type": "BUYER_SIGNAL",
          "statement": "The buyer has an active procurement programme.",
          "assumptions": ["The programme remains funded"],
          "unknowns": ["Final procurement date"],
          "implications": ["Reconfirm buyer timing"],
          "confidence_level": "LOW",
          "status": "NEEDS_REVIEW",
          "insufficient_evidence_reason": null,
          "reviewed_at": null,
          "review_note": null,
          "created_at": "2026-11-22T08:00:00+00:00",
          "updated_at": "2026-11-22T08:00:00+00:00",
          "evidence": [{
            "relation_type": "SUPPORTS",
            "rationale": "The source directly describes the programme.",
            "evidence": {
              "id": "evidence-1",
              "subject_type": "market_workspace",
              "subject_id": "workspace-1",
              "kind": "WEB_PAGE",
              "ref": "https://buyer.example.test/procurement",
              "source": "Buyer website",
              "payload": {
                "title": "Procurement programme",
                "url": "https://buyer.example.test/procurement",
                "snippet": "The programme is open for supplier submissions."
              },
              "retrieved_at": "2026-11-22T07:00:00+00:00",
              "created_at": "2026-11-22T07:00:00+00:00"
            }
          }]
        }
        """
        let insight = try DateCoding.decode(MarketInsightRead.self, fromJSON: json)
        XCTAssertEqual(insight.id, "insight-1")
        XCTAssertEqual(insight.statusLabel, "待审核")
        XCTAssertTrue(insight.canReview)
        XCTAssertEqual(insight.evidence.first?.relationType, "SUPPORTS")
        XCTAssertEqual(insight.evidence.first?.evidence.title, "Procurement programme")
        XCTAssertEqual(insight.assumptions.first?.stringValue, "The programme remains funded")
    }

    func testMarketOpportunityDetailDecodesEvidenceAndStatus() throws {
        let json = """
        {
          "opportunity": {
            "id": "opportunity-1",
            "workspace_id": "workspace-1",
            "title": "Potential procurement opportunity",
            "description": "Possible supplier engagement.",
            "status": "RESEARCHING",
            "next_action": "Confirm procurement owner",
            "qualification_reason": null,
            "disqualification_reason": null,
            "assessment_version": 1,
            "assessment_dimensions": {},
            "assessment_summary": "The buyer signal requires confirmation.",
            "assessment_confidence": "LOW",
            "created_by_run_id": null,
            "owner_user_id": null,
            "created_by_user_id": "user-1",
            "qualified_by_user_id": null,
            "qualified_at": null,
            "outcome_recorded_by_user_id": null,
            "outcome_recorded_at": null,
            "outcome_note": null,
            "created_at": "2026-11-22T08:00:00+00:00",
            "updated_at": "2026-11-22T08:00:00+00:00"
          },
          "evidence": [{
            "relation_type": "SUPPORTS",
            "rationale": "The source identifies an open procurement programme.",
            "evidence": {
              "id": "evidence-2",
              "subject_type": "market_workspace",
              "subject_id": "workspace-1",
              "kind": "WEB_PAGE",
              "ref": "https://buyer.example.test/tender",
              "source": "Buyer website",
              "payload": {"title": "Open tender"},
              "retrieved_at": "2026-11-22T07:00:00+00:00",
              "created_at": "2026-11-22T07:00:00+00:00"
            }
          }],
          "entities": [{
            "id": "entity-1",
            "entity_kind": "Distributor",
            "display_name": "East Africa Energy Group",
            "normalized_name": "east africa energy group",
            "description": null,
            "canonical_domain": "example.test",
            "country_code": "KE",
            "attributes": {},
            "status": "CANDIDATE",
            "created_at": "2026-11-24T08:00:00+00:00",
            "updated_at": "2026-11-24T08:00:00+00:00",
            "aliases": []
          }],
          "assessments": []
        }
        """
        let detail = try DateCoding.decode(MarketOpportunityDetailRead.self, fromJSON: json)
        XCTAssertEqual(detail.opportunity.id, "opportunity-1")
        XCTAssertEqual(detail.opportunity.statusLabel, "研究中")
        XCTAssertEqual(detail.evidence.first?.evidence.title, "Open tender")
        XCTAssertEqual(detail.entities.first?.id, "entity-1")
        XCTAssertEqual(detail.entities.first?.displayName, "East Africa Energy Group")
        XCTAssertTrue(detail.assessments.isEmpty)
    }

    func testMarketClaimImpactReportDecodesAffectedConsumers() throws {
        let json = """
        {
          "workspace_id": "workspace-1",
          "generated_at": "2026-11-22T12:00:00+00:00",
          "analyzed_lineage_count": 3,
          "total_lineage_count": 3,
          "affected_consumer_count": 1,
          "impact_count": 1,
          "truncated": false,
          "impacts": [{
            "claim_lineage_id": "lineage-1",
            "latest_claim_id": "claim-2",
            "latest_claim_version": 2,
            "latest_claim_status": "NEEDS_REVIEW",
            "latest_verification_verdict": "PROVISIONAL_SUPPORT",
            "current_accepted_claim_id": "claim-1",
            "consumer_type": "OPPORTUNITY",
            "consumer_id": "opportunity-1",
            "consumer_title": "Potential procurement opportunity",
            "consumer_status": "RESEARCHING",
            "relation_types": ["SUPPORTS"],
            "matched_evidence_count": 1,
            "stale_evidence_count": 0,
            "referenced_claim_versions": [1],
            "severity": "MEDIUM",
            "reason_code": "LATEST_VERSION_PENDING_REVIEW"
          }]
        }
        """
        let report = try DateCoding.decode(
            MarketClaimImpactReport.self,
            fromJSON: json
        )
        XCTAssertEqual(report.affectedConsumerCount, 1)
        XCTAssertEqual(report.impacts.first?.consumerTypeLabel, "商业机会")
        XCTAssertEqual(report.impacts.first?.reasonLabel, "最新主张版本等待人工审核")
        XCTAssertEqual(report.impacts.first?.id, "OPPORTUNITY:opportunity-1:lineage-1")
    }

    func testMarketClaimLineageHistoryDecodesVersionTimeline() throws {
        let json = """
        {
          "workspace_id": "workspace-1",
          "claim_lineage_id": "lineage-1",
          "claim_key": "fingerprint",
          "latest_version": 2,
          "current_accepted_claim_id": "claim-1",
          "versions": [{
            "id": "claim-1",
            "answer_id": "answer-1",
            "verification_run_id": "run-1",
            "claim_lineage_id": "lineage-1",
            "version": 1,
            "statement": "A named buyer published a procurement programme.",
            "claim_type": "FACT",
            "verification_verdict": "PROVISIONAL_SUPPORT",
            "confidence_level": "LOW",
            "status": "SUPERSEDED",
            "supporting_evidence_count": 1,
            "contradicting_evidence_count": 0,
            "reviewed_at": null,
            "review_note": "The latest evidence was reviewed.",
            "created_at": "2026-11-22T08:00:00+00:00"
          }]
        }
        """
        let history = try DateCoding.decode(
            MarketClaimLineageHistory.self,
            fromJSON: json
        )
        XCTAssertEqual(history.latestVersion, 2)
        XCTAssertEqual(history.versions.first?.statusLabel, "已被后续版本替代")
        XCTAssertEqual(history.currentAcceptedClaimId, "claim-1")
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
