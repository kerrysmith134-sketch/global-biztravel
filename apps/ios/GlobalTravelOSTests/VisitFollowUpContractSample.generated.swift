// 自动生成，请勿手改：scripts/gen_ios_contract_samples.py
// 独立存放 VisitFollowUp 大响应样本，避免公共样本枚举超过 SwiftLint 体积门槛。

extension ContractSamples {
    static var visitFollowUp: String {
        """
        {
          "completed_at": "2026-11-09T09:00:00+00:00",
          "completed_by_user_id": "11111111-1111-1111-1111-111111111111",
          "confirmed_at": "2026-11-09T09:00:00+00:00",
          "confirmed_by_user_id": "11111111-1111-1111-1111-111111111111",
          "created_at": "2026-11-09T09:00:00+00:00",
          "created_by_user_id": "11111111-1111-1111-1111-111111111111",
          "due_date": "1",
          "events": [
            {
              "actor_user_id": "11111111-1111-1111-1111-111111111111",
              "created_at": "2026-11-09T09:00:00+00:00",
              "event_type": "1",
              "from_status": "1",
              "id": "11111111-1111-1111-1111-111111111111",
              "note": "1",
              "to_status": "1"
            }
          ],
          "id": "11111111-1111-1111-1111-111111111111",
          "latest_reassessment": {
            "agent_run_id": "11111111-1111-1111-1111-111111111111",
            "appointment_status": "1",
            "created_at": "2026-11-09T09:00:00+00:00",
            "current_plan_version_id": "11111111-1111-1111-1111-111111111111",
            "customer_window_verification_status": "PARTIALLY_VERIFIED",
            "decision": "1",
            "delay_minutes": 1,
            "evidence_ids": [
              "11111111-1111-1111-1111-111111111111"
            ],
            "input_sha256": "1",
            "outcome": "1",
            "plan_change_recommendation": {
              "base_plan_version_id": "11111111-1111-1111-1111-111111111111",
              "base_trip_version": 1,
              "new_window_end": "2026-11-09T09:00:00+00:00",
              "new_window_start": "2026-11-09T09:00:00+00:00",
              "operation_type": "CHANGE_VISIT_TIME",
              "reason": "1",
              "requires_human_submission": true,
              "timezone": "Europe/Berlin",
              "visit_id": "11111111-1111-1111-1111-111111111111"
            },
            "projected_arrival_at": "2026-11-09T09:00:00+00:00",
            "workflow_id": "11111111-1111-1111-1111-111111111111"
          },
          "source_action_context": {
            "action_code": "1",
            "appointment_status": "1",
            "decision_status": "1",
            "integrity_status": "1",
            "priority": "1",
            "rationale": "1",
            "recommended_steps": [
              "1"
            ],
            "required_inputs": [
              "1"
            ]
          },
          "source_agent_run_id": "11111111-1111-1111-1111-111111111111",
          "source_due_date": "1",
          "source_index": 1,
          "source_record_id": "11111111-1111-1111-1111-111111111111",
          "source_title": "1",
          "source_type": "RECORD",
          "status": "OPEN",
          "title": "1",
          "trip_id": "11111111-1111-1111-1111-111111111111",
          "updated_at": "2026-11-09T09:00:00+00:00",
          "visit_id": "11111111-1111-1111-1111-111111111111"
        }
        """
    }
}
