// 自动生成，请勿手改：scripts/gen_ios_contract_samples.py
// 独立存放 VisitFollowUp 大响应样本，避免公共样本枚举超过 SwiftLint 体积门槛。

extension ContractSamples {
    static var visitFollowUp: String {
        """
        {
          "id": "11111111-1111-1111-1111-111111111111",
          "visit_id": "11111111-1111-1111-1111-111111111111",
          "trip_id": "11111111-1111-1111-1111-111111111111",
          "source_type": "RECORD",
          "source_record_id": "11111111-1111-1111-1111-111111111111",
          "source_agent_run_id": "11111111-1111-1111-1111-111111111111",
          "source_index": 1,
          "source_title": "1",
          "source_due_date": "1",
          "title": "1",
          "due_date": "1",
          "status": "OPEN",
          "created_by_user_id": "11111111-1111-1111-1111-111111111111",
          "confirmed_by_user_id": "11111111-1111-1111-1111-111111111111",
          "confirmed_at": "2026-11-09T09:00:00+00:00",
          "completed_by_user_id": "11111111-1111-1111-1111-111111111111",
          "completed_at": "2026-11-09T09:00:00+00:00",
          "created_at": "2026-11-09T09:00:00+00:00",
          "updated_at": "2026-11-09T09:00:00+00:00",
          "events": [
            {
              "id": "11111111-1111-1111-1111-111111111111",
              "event_type": "1",
              "from_status": "1",
              "to_status": "1",
              "actor_user_id": "11111111-1111-1111-1111-111111111111",
              "note": "1",
              "created_at": "2026-11-09T09:00:00+00:00"
            }
          ],
          "source_action_context": {
            "integrity_status": "1",
            "action_code": "1",
            "priority": "1",
            "decision_status": "1",
            "appointment_status": "1",
            "rationale": "1",
            "required_inputs": [
              "1"
            ],
            "recommended_steps": [
              "1"
            ]
          },
          "latest_reassessment": {
            "workflow_id": "11111111-1111-1111-1111-111111111111",
            "agent_run_id": "11111111-1111-1111-1111-111111111111",
            "outcome": "1",
            "decision": "1",
            "created_at": "2026-11-09T09:00:00+00:00",
            "input_sha256": "1",
            "current_plan_version_id": "11111111-1111-1111-1111-111111111111",
            "appointment_status": "1",
            "projected_arrival_at": "2026-11-09T09:00:00+00:00",
            "delay_minutes": 1,
            "customer_window_verification_status": "PARTIALLY_VERIFIED",
            "evidence_ids": [
              "11111111-1111-1111-1111-111111111111"
            ],
            "plan_change_recommendation": {
              "operation_type": "CHANGE_VISIT_TIME",
              "visit_id": "11111111-1111-1111-1111-111111111111",
              "base_plan_version_id": "11111111-1111-1111-1111-111111111111",
              "base_trip_version": 1,
              "new_window_start": "2026-11-09T09:00:00+00:00",
              "new_window_end": "2026-11-09T09:00:00+00:00",
              "reason": "1",
              "requires_human_submission": true
            }
          }
        }
        """
    }
}
