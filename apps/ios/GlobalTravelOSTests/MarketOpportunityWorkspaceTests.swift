import XCTest
@testable import GlobalTravelOS

final class MarketOpportunityWorkspaceTests: XCTestCase {
    func testOpportunityListCreateAndEvidenceEndpointsMatchServerRoutes() throws {
        let list = Endpoints.listMarketOpportunities(
            workspaceId: "workspace-1",
            page: PageRequest(limit: 40, cursor: "cursor-1"),
            statusFilter: "ACTION_PLANNED"
        )
        XCTAssertEqual(list.method, .get)
        XCTAssertEqual(list.path, "/v1/market/workspaces/workspace-1/opportunities")
        XCTAssertEqual(list.queryItems.map(\.name), ["limit", "cursor", "status_filter"])
        XCTAssertEqual(list.queryItems.map(\.value), ["40", "cursor-1", "ACTION_PLANNED"])
        XCTAssertNil(list.idempotencyKey)

        let create = try Endpoints.createMarketOpportunity(
            workspaceId: "workspace-1",
            body: MarketOpportunityCreateRequest(
                title: "New depot charging opportunity",
                description: "Regional fleet transition programme",
                nextAction: "Confirm procurement owner"
            )
        )
        XCTAssertEqual(create.method, .post)
        XCTAssertEqual(create.path, "/v1/market/workspaces/workspace-1/opportunities")
        XCTAssertNotNil(create.idempotencyKey)
        let createBody = try XCTUnwrap(
            JSONSerialization.jsonObject(with: XCTUnwrap(create.body)) as? [String: Any]
        )
        XCTAssertEqual(createBody["title"] as? String, "New depot charging opportunity")
        XCTAssertEqual(createBody["next_action"] as? String, "Confirm procurement owner")

        let evidenceList = Endpoints.listMarketEvidence(
            workspaceId: "workspace-1",
            page: PageRequest(limit: 50)
        )
        XCTAssertEqual(evidenceList.method, .get)
        XCTAssertEqual(evidenceList.path, "/v1/market/workspaces/workspace-1/evidence")
        XCTAssertNil(evidenceList.idempotencyKey)

        let attach = try Endpoints.addMarketOpportunityEvidence(
            workspaceId: "workspace-1",
            opportunityId: "opportunity-1",
            body: MarketOpportunityEvidenceCreateRequest(
                evidenceId: "evidence-1",
                relationType: "SUPPORTS",
                rationale: "Official procurement notice"
            )
        )
        XCTAssertEqual(
            attach.path,
            "/v1/market/workspaces/workspace-1/opportunities/opportunity-1/evidence"
        )
        XCTAssertNotNil(attach.idempotencyKey)
        let attachBody = try XCTUnwrap(
            JSONSerialization.jsonObject(with: XCTUnwrap(attach.body)) as? [String: Any]
        )
        XCTAssertEqual(attachBody["evidence_id"] as? String, "evidence-1")
        XCTAssertEqual(attachBody["relation_type"] as? String, "SUPPORTS")
    }

    func testOpportunityStateCommandsUseAuditedIdempotentRoutes() throws {
        let transition = try Endpoints.transitionMarketOpportunity(
            workspaceId: "workspace-1",
            opportunityId: "opportunity-1",
            body: MarketOpportunityTransitionRequest(
                targetStatus: "EVIDENCE_READY",
                note: "One supporting source has been attached."
            )
        )
        XCTAssertEqual(
            transition.path,
            "/v1/market/workspaces/workspace-1/opportunities/opportunity-1:transition"
        )
        XCTAssertNotNil(transition.idempotencyKey)
        let transitionBody = try XCTUnwrap(
            JSONSerialization.jsonObject(with: XCTUnwrap(transition.body)) as? [String: Any]
        )
        XCTAssertEqual(transitionBody["target_status"] as? String, "EVIDENCE_READY")

        let qualify = try Endpoints.qualifyMarketOpportunity(
            workspaceId: "workspace-1",
            opportunityId: "opportunity-1",
            body: MarketOpportunityQualifyRequest(
                qualificationReason: "Evidence supports a funded procurement programme."
            )
        )
        XCTAssertEqual(
            qualify.path,
            "/v1/market/workspaces/workspace-1/opportunities/opportunity-1:qualify"
        )
        XCTAssertNotNil(qualify.idempotencyKey)

        let outcome = try Endpoints.recordMarketOpportunityOutcome(
            workspaceId: "workspace-1",
            opportunityId: "opportunity-1",
            body: MarketOpportunityOutcomeRequest(
                outcome: "WON",
                outcomeNote: "Signed purchase order received."
            )
        )
        XCTAssertEqual(
            outcome.path,
            "/v1/market/workspaces/workspace-1/opportunities/opportunity-1:outcome"
        )
        XCTAssertNotNil(outcome.idempotencyKey)
    }

    func testOpportunityPageAndWorkspaceEvidenceRetainServerVerificationStatus() throws {
        let pageJSON = """
        {
          "items": [{
            "id": "opportunity-1",
            "workspace_id": "workspace-1",
            "title": "Fleet charging programme",
            "description": "Public procurement opportunity",
            "status": "RESEARCHING",
            "next_action": "Validate tender timeline",
            "qualification_reason": null,
            "disqualification_reason": null,
            "outcome_note": null,
            "assessment_version": 0,
            "assessment_dimensions": {},
            "assessment_summary": null,
            "assessment_confidence": "INSUFFICIENT",
            "created_by_run_id": null,
            "owner_user_id": null,
            "created_by_user_id": "user-1",
            "qualified_by_user_id": null,
            "qualified_at": null,
            "outcome_recorded_by_user_id": null,
            "outcome_recorded_at": null,
            "created_at": "2026-11-24T08:00:00+00:00",
            "updated_at": "2026-11-24T08:00:00+00:00"
          }],
          "next_cursor": "next-1"
        }
        """
        let page = try DateCoding.decode(
            Page<MarketOpportunitySummaryRead>.self,
            fromJSON: pageJSON
        )
        XCTAssertEqual(page.items.first?.id, "opportunity-1")
        XCTAssertEqual(page.items.first?.statusLabel, "研究中")
        XCTAssertEqual(page.nextCursor, "next-1")

        let evidenceJSON = """
        {
          "id": "evidence-1",
          "subject_type": "market_workspace",
          "subject_id": "workspace-1",
          "kind": "WEB_PAGE_EXTRACT",
          "ref": "market-source:v1:immutable-hash",
          "source": "Official procurement portal",
          "payload": {
            "url": "https://example.gov/procurement",
            "title": "Fleet procurement notice",
            "snippet": "The programme is open for applications."
          },
          "retrieved_at": "2026-11-24T08:10:00+00:00",
          "published_at": "2026-11-20T00:00:00+00:00",
          "canonical_ref": "https://example.gov/procurement",
          "verification_status": "VERIFIED",
          "created_at": "2026-11-24T08:10:00+00:00"
        }
        """
        let evidence = try DateCoding.decode(ResearchEvidence.self, fromJSON: evidenceJSON)
        XCTAssertEqual(evidence.verificationStatus, "VERIFIED")
        XCTAssertEqual(evidence.verificationStatusLabel, "来源已核验")
        XCTAssertEqual(evidence.canonicalRef, "https://example.gov/procurement")
    }
}
