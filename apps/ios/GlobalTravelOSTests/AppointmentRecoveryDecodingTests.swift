import XCTest
@testable import GlobalTravelOS

final class AppointmentRecoveryDecodingTests: XCTestCase {
    func testProactiveFeedDecodesAppointmentRecoveryBrief() throws {
        let json = """
        {
          "items": [{
            "id": "flight-alert-1",
            "trip_id": "trip-1",
            "suggestion_type": "FLIGHT_STATUS_ALERT",
            "status": "PENDING",
            "title": "LH123 到达延误",
            "proposed_action": {
              "kind": "PREPARE_REVIEW",
              "tool": "trip.prepare_external_review",
              "params": {
                "recovery_brief": {
                  "agent_type": "APPOINTMENT_RECOVERY",
                  "agent_version": "appointment-recovery-v1",
                  "schema_version": "1.0",
                  "execution_mode": "DETERMINISTIC",
                  "status": "RECOVERY_REQUIRED",
                  "actions": [{
                    "visit_id": "visit-1",
                    "plan_stop_id": "stop-1",
                    "action_code": "REQUEST_CUSTOMER_RESCHEDULE_WINDOW",
                    "priority": "HIGH",
                    "decision_status": "REQUIRES_HUMAN_DECISION",
                    "rationale": "预计到达晚于预约窗口结束时间。",
                    "delay_minutes": 60,
                    "required_inputs": [
                      "CUSTOMER_CONFIRMED_ALTERNATIVE_WINDOW",
                      "CUSTOMER_TIMEZONE"
                    ],
                    "candidate_windows": [],
                    "customer_message": {"status": "NOT_DRAFTED"}
                  }],
                  "data_gaps": ["CUSTOMER_CONFIRMED_ALTERNATIVE_WINDOW", "CUSTOMER_TIMEZONE"],
                  "guardrails": {
                    "human_decision_required": true,
                    "plan_mutated": false,
                    "booking_changed": false,
                    "customer_contacted": false,
                    "customer_message_sent": false,
                    "candidate_windows_inferred": false,
                    "candidate_windows": []
                  }
                }
              }
            },
            "requires_approval": true,
            "severity": "HIGH"
          }],
          "daily_cap": {"used": 0, "cap": 3},
          "next_cursor": null
        }
        """
        let feed = try DateCoding.decode(SuggestionFeed.self, fromJSON: json)
        let suggestion = try XCTUnwrap(feed.items.first)
        let brief = try XCTUnwrap(suggestion.recoveryBrief)

        XCTAssertEqual(brief.status, "RECOVERY_REQUIRED")
        XCTAssertEqual(brief.statusLabel, "需要安排恢复")
        XCTAssertEqual(brief.actions.count, 1)
        XCTAssertEqual(brief.actions[0].actionLabel, "征求客户改期窗口")
        XCTAssertEqual(brief.actions[0].priorityLabel, "高优先级")
        XCTAssertEqual(brief.actions[0].visitId, "visit-1")
        XCTAssertEqual(brief.actions[0].delayMinutes, 60)
        XCTAssertEqual(brief.actions[0].requiredInputs.count, 2)
        XCTAssertEqual(brief.dataGaps.count, 2)
        XCTAssertTrue(brief.humanDecisionRequired)
        XCTAssertFalse(brief.planMutated)
        XCTAssertFalse(brief.bookingChanged)
        XCTAssertFalse(brief.customerContacted)
        XCTAssertFalse(brief.candidateWindowsInferred)
    }
}
