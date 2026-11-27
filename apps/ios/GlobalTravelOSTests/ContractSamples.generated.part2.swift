// 自动生成，请勿手改：scripts/gen_ios_contract_samples.py
// ContractSamples 分片；与主样本文件由同一 OpenAPI schema 生成。

extension ContractSamples {
    static let marketEvidenceIntelligenceReport = """
{
  "analyzed_evidence_count": 1,
  "caveats": [
    "1"
  ],
  "disputed_count": 1,
  "distinct_host_count": 1,
  "duplicate_content_evidence_count": 1,
  "duplicate_content_group_count": 1,
  "generated_at": "2026-11-09T09:00:00+00:00",
  "is_truncated": true,
  "items": [
    {
      "age_days": 1,
      "canonical_ref": "1",
      "content_fingerprint": "1",
      "derived_page_evidence_ids": [
        "11111111-1111-1111-1111-111111111111"
      ],
      "duplicate_content_evidence_ids": [
        "11111111-1111-1111-1111-111111111111"
      ],
      "evidence_id": "11111111-1111-1111-1111-111111111111",
      "fingerprint_source": "STORED_HASH",
      "issues": [
        "1"
      ],
      "kind": "1",
      "origin_assessment": "DUPLICATE_CONTENT",
      "published_at": "2026-11-09T09:00:00+00:00",
      "retrieved_at": "2026-11-09T09:00:00+00:00",
      "same_host_evidence_ids": [
        "11111111-1111-1111-1111-111111111111"
      ],
      "same_source_version_evidence_ids": [
        "11111111-1111-1111-1111-111111111111"
      ],
      "source": "1",
      "source_host": "1",
      "verification_status": "UNVERIFIED"
    }
  ],
  "missing_content_hash_count": 1,
  "missing_publication_date_count": 1,
  "multi_version_evidence_count": 1,
  "multi_version_source_count": 1,
  "partially_verified_count": 1,
  "rejected_count": 1,
  "total_evidence_count": 1,
  "unextracted_search_result_count": 1,
  "unverified_count": 1,
  "verified_count": 1,
  "workspace_id": "11111111-1111-1111-1111-111111111111"
}
"""
    static let marketSourceVerificationPlan = """
{
  "action_count": 1,
  "actions": [
    {
      "action_id": "1",
      "action_type": "EXTRACT_SOURCE_PAGE",
      "claim_id": "11111111-1111-1111-1111-111111111111",
      "evidence_ids": [
        "11111111-1111-1111-1111-111111111111"
      ],
      "priority": "HIGH",
      "rationale": "1",
      "research_question_id": "11111111-1111-1111-1111-111111111111",
      "research_question_status": "OPEN",
      "target_id": "11111111-1111-1111-1111-111111111111",
      "target_type": "EVIDENCE",
      "title": "1"
    }
  ],
  "actions_truncated": true,
  "analyzed_claim_count": 1,
  "analyzed_evidence_count": 1,
  "claims": [
    {
      "changed_source_count": 1,
      "claim_id": "11111111-1111-1111-1111-111111111111",
      "claim_lineage_id": "11111111-1111-1111-1111-111111111111",
      "claim_status": "NEEDS_REVIEW",
      "claim_type": "FACT",
      "claim_version": 1,
      "contradicting_evidence_count": 1,
      "distinct_content_count": 1,
      "distinct_host_count": 1,
      "evidence": [
        {
          "content_changed_since_assessment": true,
          "content_fingerprint": "1",
          "evidence_id": "11111111-1111-1111-1111-111111111111",
          "fingerprint_source": "STORED_HASH",
          "issues": [
            "1"
          ],
          "latest_source_evidence_id": "11111111-1111-1111-1111-111111111111",
          "relation_type": "SUPPORTS",
          "source_host": "1",
          "source_version_changed": true,
          "status_changed_since_assessment": true,
          "unextracted": true
        }
      ],
      "issues": [
        "1"
      ],
      "source_origin_state": "NO_SUPPORTING_EVIDENCE",
      "statement": "1",
      "status_changed_source_count": 1,
      "supporting_evidence_count": 1,
      "unextracted_source_count": 1,
      "verification_verdict": "PROVISIONAL_SUPPORT"
    }
  ],
  "claims_needing_corroboration_count": 1,
  "claims_truncated": true,
  "claims_with_changed_sources_count": 1,
  "claims_with_contradictions_count": 1,
  "evidence_truncated": true,
  "generated_at": "2026-11-09T09:00:00+00:00",
  "limitations": [
    "1"
  ],
  "total_claim_count": 1,
  "total_evidence_count": 1,
  "workspace_id": "11111111-1111-1111-1111-111111111111"
}
"""
    static let marketSourceVerificationPlanApplication = """
{
  "action_count": 1,
  "applied_at": "2026-11-09T09:00:00+00:00",
  "created_question_count": 1,
  "dependency_count": 1,
  "plan_digest": "1",
  "reused_application": true,
  "reused_question_count": 1,
  "tasks": [
    {
      "action_id": "1",
      "action_type": "EXTRACT_SOURCE_PAGE",
      "depends_on_question_ids": [
        "11111111-1111-1111-1111-111111111111"
      ],
      "priority": "HIGH",
      "question": "1",
      "question_id": "11111111-1111-1111-1111-111111111111",
      "question_status": "OPEN",
      "reused": true
    }
  ],
  "workspace_id": "11111111-1111-1111-1111-111111111111"
}
"""
    static let minutesDraft = """
{
  "decisions": [
    "1"
  ],
  "degraded": true,
  "draft_id": "11111111-1111-1111-1111-111111111111",
  "follow_up_tasks": [
    {
      "due_date": "1",
      "title": "1"
    }
  ],
  "insights": [
    {
      "kind": "SUMMARY",
      "requires_confirmation": true,
      "text": "1"
    }
  ],
  "llm_invocation_id": "11111111-1111-1111-1111-111111111111",
  "summary": "1",
  "title": "1"
}
"""
    static let notificationFeed = """
{
  "items": [
    {
      "attempts": 1,
      "body": "1",
      "created_at": "2026-11-09T09:00:00+00:00",
      "id": "11111111-1111-1111-1111-111111111111",
      "issue_key": "1",
      "kind": "1",
      "priority": "P0",
      "provider": "1",
      "read_at": "2026-11-09T09:00:00+00:00",
      "sent_at": "2026-11-09T09:00:00+00:00",
      "status": "PENDING",
      "suggestion_id": "11111111-1111-1111-1111-111111111111",
      "title": "1"
    }
  ],
  "unread": 1,
  "unread_by_priority": {
    "key": 1
  }
}
"""
    static let notificationPreferenceRead = """
{
  "muted_priorities": [
    "P0"
  ]
}
"""
    static let place = """
{
  "address_line": "1",
  "city": "1",
  "country_code": "1",
  "created_at": "2026-11-09T09:00:00+00:00",
  "geocode_confidence": "1",
  "geocode_source": "1",
  "geocode_status": "MISSING",
  "id": "11111111-1111-1111-1111-111111111111",
  "lat": 1.5,
  "lon": 1.5,
  "name": "1",
  "region": "1",
  "timezone": "1",
  "updated_at": "2026-11-09T09:00:00+00:00"
}
"""
    static let planDiff = """
{
  "base_degraded": true,
  "base_plan_version_id": "11111111-1111-1111-1111-111111111111",
  "base_solver": "ortools",
  "base_status": "DRAFT",
  "base_version_number": 1,
  "objective_delta": 1.5,
  "objective_from": 1.5,
  "objective_to": 1.5,
  "stops": [
    {
      "arrive_at_from": "2026-11-09T09:00:00+00:00",
      "arrive_at_to": "2026-11-09T09:00:00+00:00",
      "change": "ADDED",
      "day_index_from": 1,
      "day_index_to": 1,
      "depart_at_from": "2026-11-09T09:00:00+00:00",
      "depart_at_to": "2026-11-09T09:00:00+00:00",
      "sequence_from": 1,
      "sequence_to": 1,
      "service_minutes_from": 1,
      "service_minutes_to": 1,
      "travel_minutes_from": 1,
      "travel_minutes_to": 1,
      "visit_id": "11111111-1111-1111-1111-111111111111"
    }
  ],
  "summary": {
    "key": 1
  },
  "target_degraded": true,
  "target_plan_version_id": "11111111-1111-1111-1111-111111111111",
  "target_solver": "ortools",
  "target_status": "DRAFT",
  "target_version_number": 1,
  "travel_minutes_delta": 1,
  "travel_minutes_from": 1,
  "travel_minutes_to": 1,
  "trip_id": "11111111-1111-1111-1111-111111111111",
  "unplaced": [
    {
      "action": "ADDED",
      "reason": "1",
      "source": "1",
      "visit_id": "11111111-1111-1111-1111-111111111111"
    }
  ]
}
"""
    static let planVersion = """
{
  "activated_at": "2026-11-09T09:00:00+00:00",
  "cost_currency": "1",
  "cost_total": 1.5,
  "cost_unknown_count": 1,
  "created_at": "2026-11-09T09:00:00+00:00",
  "day_count": 1,
  "degraded": true,
  "explanation_ref": "1",
  "id": "11111111-1111-1111-1111-111111111111",
  "job_id": "11111111-1111-1111-1111-111111111111",
  "objective_value": 1.5,
  "policy_violations": [
    {
      "code": "1",
      "details": {},
      "message": "1"
    }
  ],
  "problem_hash": "1",
  "solution_status": "OPTIMAL",
  "solver": "ortools",
  "status": "DRAFT",
  "stop_count": 1,
  "superseded_at": "2026-11-09T09:00:00+00:00",
  "trip_id": "11111111-1111-1111-1111-111111111111",
  "trip_version": 1,
  "unplaced": [
    {
      "detail": "1",
      "reason": "1",
      "source": "1",
      "visit_id": "1"
    }
  ],
  "updated_at": "2026-11-09T09:00:00+00:00",
  "validation": {},
  "version_number": 1
}
"""
    static let planVersionDetail = """
{
  "activated_at": "2026-11-09T09:00:00+00:00",
  "cost_currency": "1",
  "cost_total": 1.5,
  "cost_unknown_count": 1,
  "created_at": "2026-11-09T09:00:00+00:00",
  "day_count": 1,
  "degraded": true,
  "explanation_ref": "1",
  "id": "11111111-1111-1111-1111-111111111111",
  "job_id": "11111111-1111-1111-1111-111111111111",
  "legs": [
    {
      "cost_amount": 1.5,
      "cost_currency": "1",
      "day_index": 1,
      "distance_km": 1.5,
      "from_stop_id": "11111111-1111-1111-1111-111111111111",
      "id": "11111111-1111-1111-1111-111111111111",
      "mode": "GROUND",
      "source": "1",
      "stale": true,
      "to_stop_id": "11111111-1111-1111-1111-111111111111",
      "travel_minutes": 1
    }
  ],
  "objective_value": 1.5,
  "policy_violations": [
    {
      "code": "1",
      "details": {},
      "message": "1"
    }
  ],
  "problem_hash": "1",
  "solution_status": "OPTIMAL",
  "solver": "ortools",
  "status": "DRAFT",
  "stop_count": 1,
  "stops": [
    {
      "arrive_at": "2026-11-09T09:00:00+00:00",
      "city": "1",
      "cost_amount": 1.5,
      "cost_currency": "1",
      "country_code": "1",
      "day_date": "2026-11-09",
      "day_index": 1,
      "depart_at": "2026-11-09T09:00:00+00:00",
      "distance_km": 1.5,
      "id": "11111111-1111-1111-1111-111111111111",
      "latitude": 1.5,
      "longitude": 1.5,
      "place_id": "11111111-1111-1111-1111-111111111111",
      "place_name": "1",
      "sequence": 1,
      "service_minutes": 1,
      "travel_minutes": 1,
      "travel_source": "1",
      "travel_stale": true,
      "visit_id": "11111111-1111-1111-1111-111111111111"
    }
  ],
  "superseded_at": "2026-11-09T09:00:00+00:00",
  "trip_id": "11111111-1111-1111-1111-111111111111",
  "trip_version": 1,
  "unplaced": [
    {
      "detail": "1",
      "reason": "1",
      "source": "1",
      "visit_id": "1"
    }
  ],
  "updated_at": "2026-11-09T09:00:00+00:00",
  "validation": {},
  "version_number": 1
}
"""
}
