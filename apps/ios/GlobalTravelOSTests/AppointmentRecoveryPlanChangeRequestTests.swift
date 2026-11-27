import XCTest
@testable import GlobalTravelOS

final class AppointmentRecoveryPlanChangeRequestTests: XCTestCase {
    func testCustomerAppointmentWindowEncodesOffsetsFromIanaTimezoneAcrossDST() throws {
        let parser = ISO8601DateFormatter()
        let start = try XCTUnwrap(parser.date(from: "2027-03-28T00:30:00Z"))
        let end = try XCTUnwrap(parser.date(from: "2027-03-28T01:30:00Z"))
        let request = CustomerAppointmentWindowAttestationRequest(
            startAt: start,
            endAt: end,
            timezone: "Europe/Berlin",
            confirmationMethod: "EMAIL",
            confirmationReference: nil
        )

        let data = try DateCoding.encode(request)
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        let startAt = try XCTUnwrap(object["start_at"] as? String)
        let endAt = try XCTUnwrap(object["end_at"] as? String)

        XCTAssertTrue(startAt.hasSuffix("+01:00"), startAt)
        XCTAssertTrue(endAt.hasSuffix("+02:00"), endAt)
        XCTAssertEqual(object["timezone"] as? String, "Europe/Berlin")
    }

    func testPlanVersionDetailDecodesItsTripVersionForActivationGuard() throws {
        let plan = try DateCoding.decode(
            PlanVersionDetail.self,
            fromJSON: ContractSamples.planVersionDetail
        )

        XCTAssertNotNil(plan.tripVersion)
    }

    func testRecoveryPlanChangeUsesExistingPlanChangeContract() throws {
        let request = PlanChangeCreate(
            basePlanVersionId: "11111111-1111-1111-1111-111111111111",
            baseTripVersion: 12,
            operationType: .changeVisitTime,
            payload: [
                "visit_id": "22222222-2222-2222-2222-222222222222",
                "recovery_follow_up_id": "33333333-3333-3333-3333-333333333333",
                "new_window_start": "2027-03-28T01:30:00.000Z",
                "new_window_end": "2027-03-28T02:30:00.000Z"
            ],
            source: "APPOINTMENT_RECOVERY",
            reason: "Evidence-backed appointment recovery"
        )

        let data = try DateCoding.encode(request)
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        let payload = try XCTUnwrap(object["payload"] as? [String: String])

        XCTAssertEqual(object["base_plan_version_id"] as? String, request.basePlanVersionId)
        XCTAssertEqual(object["base_trip_version"] as? Int, 12)
        XCTAssertEqual(object["source"] as? String, "APPOINTMENT_RECOVERY")
        XCTAssertEqual(object["operation_type"] as? String, "CHANGE_VISIT_TIME")
        XCTAssertEqual(payload["visit_id"], "22222222-2222-2222-2222-222222222222")
        XCTAssertEqual(payload["recovery_follow_up_id"], "33333333-3333-3333-3333-333333333333")
        XCTAssertEqual(payload["new_window_start"], "2027-03-28T01:30:00.000Z")
        XCTAssertEqual(payload["new_window_end"], "2027-03-28T02:30:00.000Z")
    }
}
