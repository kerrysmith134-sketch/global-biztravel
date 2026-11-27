import XCTest
@testable import GlobalTravelOS

final class TripRiskContractTests: XCTestCase {
    func testTripRiskCaseContractSampleDecodes() throws {
        let value = try DateCoding.decode(TripRiskCase.self, fromJSON: ContractSamples.tripRiskCase)
        XCTAssertEqual(value.status, "OPEN")
        XCTAssertEqual(value.severity, "LOW")
        XCTAssertEqual(value.version, 1)
        XCTAssertNotNil(value.firstSeenAt)
    }

    func testTripRiskCaseEventContractSamplePreservesCaseVersion() throws {
        let value = try DateCoding.decode(
            TripRiskCaseEvent.self,
            fromJSON: ContractSamples.tripRiskCaseEvent
        )
        XCTAssertEqual(value.caseVersion, 1)
        XCTAssertEqual(value.actorType, "SYSTEM")
        XCTAssertEqual(value.toStatus, "OPEN")
    }

    func testTripRiskReconciliationContractSampleDecodes() throws {
        let value = try DateCoding.decode(
            TripRiskReconciliation.self,
            fromJSON: ContractSamples.tripRiskReconciliation
        )
        XCTAssertEqual(value.casesCreated, 1)
        XCTAssertEqual(value.errors, ["1"])
        XCTAssertEqual(value.summary, "新增 1 · 更新 1 · 重新开启 1 · 升级 1")
    }

    func testTripRiskTransitionEnvelopeUsesBackendCaseKey() throws {
        let value = try DateCoding.decode(
            TripRiskTransitionResponse.self,
            fromJSON: ContractSamples.tripRiskTransition
        )
        XCTAssertEqual(value.caseItem.id, "11111111-1111-1111-1111-111111111111")
        XCTAssertEqual(value.event.caseVersion, 1)
    }

    func testListTripRiskCasesEndpointIncludesFilterAndClampsLimit() {
        let request = Endpoints.listTripRiskCases(
            tripId: "trip-1",
            status: "OPEN",
            limit: 999
        )
        XCTAssertEqual(request.method, .get)
        XCTAssertEqual(request.path, "/v1/trips/trip-1/risk-cases")
        XCTAssertEqual(request.queryItems.first(where: { $0.name == "status" })?.value, "OPEN")
        XCTAssertEqual(request.queryItems.first(where: { $0.name == "limit" })?.value, "200")
        XCTAssertNil(request.idempotencyKey)
    }

    func testTripRiskEventEndpointCapsHistoryAndUsesNestedPath() {
        let request = Endpoints.listTripRiskEvents(
            tripId: "trip-1",
            caseId: "case-1",
            limit: 900
        )
        XCTAssertEqual(request.method, .get)
        XCTAssertEqual(request.path, "/v1/trips/trip-1/risk-cases/case-1/events")
        XCTAssertEqual(request.queryItems.first?.value, "500")
    }

    func testTripRiskWritesAreIdempotent() {
        let request = Endpoints.reconcileTripRiskCases(tripId: "trip-1")
        XCTAssertEqual(request.method, .post)
        XCTAssertEqual(request.path, "/v1/trips/trip-1/risk-cases:reconcile")
        XCTAssertNotNil(request.idempotencyKey)
    }

    func testTripRiskTransitionEncodesExpectedVersionAndBackendEnum() throws {
        let request = try Endpoints.transitionTripRiskCase(
            tripId: "trip-1",
            caseId: "case-1",
            request: TripRiskTransitionRequest(
                expectedVersion: 7,
                toStatus: .waitingExternal,
                note: "等待客户确认新的预约时间",
                nextReviewAt: Date(timeIntervalSince1970: 1_800_000_000)
            )
        )
        XCTAssertEqual(request.method, .post)
        XCTAssertEqual(request.path, "/v1/trips/trip-1/risk-cases/case-1:transition")
        XCTAssertNotNil(request.idempotencyKey)

        let data = try XCTUnwrap(request.body)
        let payload = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(payload["expected_version"] as? Int, 7)
        XCTAssertEqual(payload["to_status"] as? String, "WAITING_EXTERNAL")
        XCTAssertEqual(payload["note"] as? String, "等待客户确认新的预约时间")
        XCTAssertNotNil(payload["next_review_at"])
        XCTAssertNil(payload["resolution_basis"])
    }

    func testTripRiskEventsUseCaseVersionAsCausalOrder() throws {
        let sample = try DateCoding.decode(
            TripRiskCaseEvent.self,
            fromJSON: ContractSamples.tripRiskCaseEvent
        )
        var first = sample
        first.id = "event-v1"
        first.caseVersion = 1
        // Deliberately later timestamp: DB timestamps are not the causal sequence.
        first.createdAt = Date(timeIntervalSince1970: 300)

        var second = sample
        second.id = "event-v2"
        second.caseVersion = 2
        second.createdAt = Date(timeIntervalSince1970: 100)

        var third = sample
        third.id = "event-v3"
        third.caseVersion = 3
        third.createdAt = Date(timeIntervalSince1970: 100)

        let ordered = TripRiskCaseEvent.orderedByCaseVersion([third, first, second])
        XCTAssertEqual(ordered.map(\.caseVersion), [1, 2, 3])
        XCTAssertEqual(ordered.map(\.id), ["event-v1", "event-v2", "event-v3"])
    }

    func testTripRiskResolutionBasisUsesBackendEnumValues() throws {
        XCTAssertEqual(
            Set(TripRiskResolutionBasis.allCases.map(\\.rawValue)),
            Set([
                "PLAN_REVALIDATED",
                "FOLLOW_UP_COMPLETED",
                "TRIP_CLOSED",
                "HUMAN_ATTESTATION",
                "FALSE_POSITIVE_CONFIRMED",
                "FOLLOW_UP_DISMISSED"
            ])
        )

        let request = try Endpoints.transitionTripRiskCase(
            tripId: "trip-1",
            caseId: "case-1",
            request: TripRiskTransitionRequest(
                expectedVersion: 2,
                toStatus: .resolved,
                note: "人工确认风险已解决",
                resolutionBasis: .humanAttestation
            )
        )
        let data = try XCTUnwrap(request.body)
        let payload = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        XCTAssertEqual(payload["resolution_basis"] as? String, "HUMAN_ATTESTATION")
    }

    func testTripRiskTerminalAndUnknownStatusesOfferNoManualTransitions() throws {
        let value = try DateCoding.decode(TripRiskCase.self, fromJSON: ContractSamples.tripRiskCase)
        var terminal = value
        terminal.status = "RESOLVED"
        XCTAssertTrue(terminal.isTerminal)
        XCTAssertTrue(terminal.availableTransitions.isEmpty)

        var unknown = value
        unknown.status = "FUTURE_BACKEND_STATE"
        XCTAssertTrue(unknown.availableTransitions.isEmpty)
    }
}
