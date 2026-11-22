import XCTest
@testable import GlobalTravelOS

final class MarketOpportunityExecutionTests: XCTestCase {
    func testMarketOpportunityExecutionEndpointsAreExplicitAndIdempotent() throws {
            let execution = Endpoints.marketOpportunityExecution(
                workspaceId: "workspace-1",
                opportunityId: "opportunity-1"
            )
            XCTAssertEqual(execution.method, .get)
            XCTAssertEqual(
                execution.path,
                "/v1/market/workspaces/workspace-1/opportunities/opportunity-1/execution"
            )
            XCTAssertNil(execution.idempotencyKey)
            XCTAssertNil(execution.body)
    
            let link = try Endpoints.linkMarketOpportunityVisit(
                workspaceId: "workspace-1",
                opportunityId: "opportunity-1",
                body: MarketOpportunityVisitLinkCreate(
                    tripId: "trip-1",
                    visitId: "visit-1",
                    linkNote: "Buyer follow-up meeting"
                )
            )
            XCTAssertEqual(link.method, .post)
            XCTAssertEqual(
                link.path,
                "/v1/market/workspaces/workspace-1/opportunities/opportunity-1/visits"
            )
            XCTAssertNotNil(link.idempotencyKey)
            let linkPayload = try XCTUnwrap(
                JSONSerialization.jsonObject(with: XCTUnwrap(link.body)) as? [String: Any]
            )
            XCTAssertEqual(linkPayload["trip_id"] as? String, "trip-1")
            XCTAssertEqual(linkPayload["visit_id"] as? String, "visit-1")
            XCTAssertEqual(linkPayload["link_note"] as? String, "Buyer follow-up meeting")
    
            let unlink = try Endpoints.unlinkMarketOpportunityVisit(
                workspaceId: "workspace-1",
                opportunityId: "opportunity-1",
                visitId: "visit-1",
                body: MarketOpportunityVisitUnlinkRequest(unlinkNote: "Meeting rescheduled")
            )
            XCTAssertEqual(unlink.method, .post)
            XCTAssertEqual(
                unlink.path,
                "/v1/market/workspaces/workspace-1/opportunities/opportunity-1/visits/visit-1:unlink"
            )
            XCTAssertNotNil(unlink.idempotencyKey)
            let unlinkPayload = try XCTUnwrap(
                JSONSerialization.jsonObject(with: XCTUnwrap(unlink.body)) as? [String: Any]
            )
            XCTAssertEqual(unlinkPayload["unlink_note"] as? String, "Meeting rescheduled")
        }
    
        func testMarketOpportunityExecutionDecodesCandidatesAndLinkHistory() throws {
            let json = """
            {
              "workspace_id": "workspace-1",
              "opportunity_id": "opportunity-1",
              "opportunity_status": "ACTION_PLANNED",
              "can_link_visits": true,
              "blocked_reason": null,
              "links": [{
                "id": "link-1",
                "workspace_id": "workspace-1",
                "opportunity_id": "opportunity-1",
                "link_status": "LINKED",
                "link_note": "Buyer follow-up",
                "linked_by_user_id": "user-1",
                "linked_at": "2026-11-22T08:00:00+00:00",
                "unlinked_by_user_id": null,
                "unlinked_at": null,
                "unlink_note": null,
                "trip_id": "trip-1",
                "trip_name": "Nairobi buyer meetings",
                "trip_status": "READY",
                "trip_window_start": "2026-12-10T00:00:00+03:00",
                "trip_window_end": "2026-12-12T23:59:00+03:00",
                "visit_id": "visit-1",
                "visit_title": "Procurement follow-up",
                "visit_objective": "Confirm tender timeline",
                "visit_status": "SCHEDULED",
                "customer_id": "customer-1",
                "customer_name": "Example Buyer",
                "place_id": "place-1",
                "place_name": "Buyer Office",
                "city": "Nairobi",
                "country_code": "KE",
                "latitude": -1.2864,
                "longitude": 36.8172,
                "timezone": "Africa/Nairobi",
                "time_window_start": "2026-12-11T10:00:00+03:00",
                "time_window_end": "2026-12-11T11:00:00+03:00",
                "service_minutes": 60,
                "priority": 1,
                "business_value": "125000.00",
                "movement_mode": "GROUND"
              }],
              "candidates": [{
                "trip_id": "trip-2",
                "trip_name": "Mombasa buyer meetings",
                "trip_status": "PLANNING",
                "trip_window_start": "2026-12-15T00:00:00+03:00",
                "trip_window_end": "2026-12-17T23:59:00+03:00",
                "visit_id": "visit-2",
                "visit_title": "Technical discovery",
                "visit_objective": "Understand charging requirements",
                "visit_status": "PLANNED",
                "customer_id": null,
                "customer_name": null,
                "place_id": "place-2",
                "place_name": "Mombasa Industrial Area",
                "city": "Mombasa",
                "country_code": "KE",
                "latitude": -4.0435,
                "longitude": 39.6682,
                "timezone": "Africa/Nairobi",
                "time_window_start": null,
                "time_window_end": null,
                "service_minutes": 90,
                "priority": 2,
                "business_value": 0.00,
                "movement_mode": "GROUND"
              }]
            }
            """
            let execution = try DateCoding.decode(
                MarketOpportunityExecutionRead.self,
                fromJSON: json
            )
            XCTAssertEqual(execution.opportunityStatus, "ACTION_PLANNED")
            XCTAssertTrue(execution.canLinkVisits)
            XCTAssertNil(execution.blockedReason)
            XCTAssertEqual(execution.links.first?.id, "link-1")
            XCTAssertTrue(execution.links.first?.isLinked == true)
            XCTAssertEqual(execution.links.first?.customerName, "Example Buyer")
        XCTAssertEqual(execution.links.first?.businessValue, Decimal(string: "125000.00"))
            XCTAssertEqual(execution.candidates.first?.visitId, "visit-2")
        XCTAssertEqual(execution.candidates.first?.businessValue, Decimal(string: "0.00"))
            XCTAssertEqual(execution.candidates.first?.locationLabel, "Mombasa · KE")
        }
}
