import XCTest
@testable import GlobalTravelOS

final class AppointmentRecoveryReassessmentDecodingTests: XCTestCase {
    func testFollowUpContractSampleDecodesLatestReassessmentAndPlanProposal() throws {
        let item = try DateCoding.decode(
            VisitFollowUp.self,
            fromJSON: ContractSamples.visitFollowUp
        )

        let reassessment = try XCTUnwrap(item.latestReassessment)
        XCTAssertEqual(reassessment.customerWindowVerificationStatus, "PARTIALLY_VERIFIED")
        XCTAssertNotNil(reassessment.projectedArrivalAt)
        XCTAssertNotNil(reassessment.planChangeRecommendation)
        XCTAssertEqual(
            reassessment.planChangeRecommendation?.operationType,
            "CHANGE_VISIT_TIME"
        )
        XCTAssertEqual(reassessment.planChangeRecommendation?.timezone, "Europe/Berlin")
    }
}
