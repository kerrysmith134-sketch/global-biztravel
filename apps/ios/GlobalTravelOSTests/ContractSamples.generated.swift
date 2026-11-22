// 自动生成，请勿手改：scripts/gen_ios_contract_samples.py
// 数据源：contracts/openapi.json（S6 契约漂移门禁；make ios-contract-samples 重新生成）
//
// 这些样本让 ModelDecodingTests 之外再有一层“契约→解码”检查：
// 后端 schema 改了字段名/类型，对应样本随之变化，解码测试会立刻失败。

enum ContractSamples {
    static let actionCenter = """
{
  "counts": {
    "key": 1
  },
  "generated_at": "2026-11-09T09:00:00+00:00",
  "sections": [
    {
      "items": [
        {
          "action_hint": "1",
          "available_actions": [
            "1"
          ],
          "category": "DECISION",
          "created_at": "2026-11-09T09:00:00+00:00",
          "detail": "1",
          "entity_id": "1",
          "kind": "SUGGESTION",
          "severity": "1",
          "status": "1",
          "title": "1",
          "trip_id": "1",
          "trip_name": "1",
          "updated_at": "2026-11-09T09:00:00+00:00"
        }
      ],
      "key": "DECISION",
      "title": "1"
    }
  ],
  "total": 1
}
"""
    static let appNotification = """
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
"""
    static let assistantConversationDetail = """
{
  "conversation": {
    "created_at": "2026-11-09T09:00:00+00:00",
    "id": "11111111-1111-1111-1111-111111111111",
    "title": "1",
    "trip_id": "11111111-1111-1111-1111-111111111111",
    "updated_at": "2026-11-09T09:00:00+00:00"
  },
  "messages": [
    {
      "cards": [
        {
          "detail": "1",
          "entity_id": "1",
          "link": "1",
          "payload": {},
          "proposal_id": "1",
          "title": "1",
          "type": "1"
        }
      ],
      "content": "1",
      "created_at": "2026-11-09T09:00:00+00:00",
      "id": "11111111-1111-1111-1111-111111111111",
      "llm_invocation_id": "11111111-1111-1111-1111-111111111111",
      "role": "1"
    }
  ]
}
"""
    static let budgetSummary = """
{
  "actual_by_currency": [
    {
      "amount": 1.5,
      "count": 1,
      "currency": "1"
    }
  ],
  "actual_total_in_budget_currency": "1",
  "budget_amount": "1",
  "budget_currency": "1",
  "conversion_base_currency": "1",
  "conversion_complete": true,
  "converted_actual_total": "1",
  "converted_planned_cost_total": "1",
  "converted_variance_vs_budget": "1",
  "fx_conversions": [
    {
      "as_of": "2026-11-09T09:00:00+00:00",
      "currency": "1",
      "rate": 1.5,
      "source": "1",
      "via_inverse": true
    }
  ],
  "planned_cost_currency": "1",
  "planned_cost_total": 1.5,
  "planned_cost_unknown_count": 1,
  "unconverted_currencies": [
    "1"
  ],
  "variance_vs_budget": "1",
  "variance_vs_plan": 1.5
}
"""
    static let crmSyncJob = """
{
  "created_at": "2026-11-09T09:00:00+00:00",
  "error": "1",
  "id": "11111111-1111-1111-1111-111111111111",
  "job_id": "11111111-1111-1111-1111-111111111111",
  "result": {},
  "status": "QUEUED",
  "trip_id": "11111111-1111-1111-1111-111111111111",
  "updated_at": "2026-11-09T09:00:00+00:00",
  "visit_id": "11111111-1111-1111-1111-111111111111",
  "visit_record_id": "11111111-1111-1111-1111-111111111111"
}
"""
    static let customer = """
{
  "country_code": "1",
  "created_at": "2026-11-09T09:00:00+00:00",
  "external_ref": "1",
  "id": "11111111-1111-1111-1111-111111111111",
  "industry": "1",
  "name": "1",
  "owner_user_id": "11111111-1111-1111-1111-111111111111",
  "primary_contact_email": "1",
  "primary_contact_name": "1",
  "primary_contact_phone": "1",
  "status": "ACTIVE",
  "tier": "A",
  "updated_at": "2026-11-09T09:00:00+00:00"
}
"""
    static let entryAssessment = """
{
  "assessed_at": "2026-11-09T09:00:00+00:00",
  "confidence": 1.5,
  "country_code": "1",
  "created_at": "2026-11-09T09:00:00+00:00",
  "id": "11111111-1111-1111-1111-111111111111",
  "manual_review_required": true,
  "source": "1",
  "source_version": "1",
  "summary": "1",
  "trip_id": "11111111-1111-1111-1111-111111111111"
}
"""
    static let expenseReport = """
{
  "created_at": "2026-11-09T09:00:00+00:00",
  "decided_at": "2026-11-09T09:00:00+00:00",
  "decided_by_user_id": "11111111-1111-1111-1111-111111111111",
  "id": "11111111-1111-1111-1111-111111111111",
  "items": [
    {
      "amount": "1",
      "category": "TRANSPORT",
      "currency": "1",
      "expense_id": "11111111-1111-1111-1111-111111111111",
      "id": "11111111-1111-1111-1111-111111111111",
      "note": "1",
      "occurred_on": "2026-11-09",
      "receipt_ref": "1",
      "report_id": "11111111-1111-1111-1111-111111111111"
    }
  ],
  "notes": "1",
  "status": "DRAFT",
  "submitted_at": "2026-11-09T09:00:00+00:00",
  "totals": [
    {
      "amount": 1.5,
      "count": 1,
      "currency": "1"
    }
  ],
  "trip_id": "11111111-1111-1111-1111-111111111111",
  "updated_at": "2026-11-09T09:00:00+00:00"
}
"""
    static let importParseResult = """
{
  "columns": [
    "1"
  ],
  "duplicate_rows": 1,
  "file_sha256": "1",
  "filename": "1",
  "import_id": "11111111-1111-1111-1111-111111111111",
  "invalid_rows": 1,
  "missing_columns": [
    "1"
  ],
  "report": {},
  "rows": [
    {
      "issues": [
        {
          "code": "1",
          "field": "1",
          "message": "1",
          "severity": "ERROR"
        }
      ],
      "normalized": {},
      "raw": {},
      "row_number": 1,
      "status": "1"
    }
  ],
  "status": "1",
  "total_rows": 1,
  "trip_id": "11111111-1111-1111-1111-111111111111",
  "unknown_columns": [
    "1"
  ],
  "valid_rows": 1
}
"""
    static let jobResult = """
{
  "attempts": 1,
  "available_at": "2026-11-09T09:00:00+00:00",
  "created_at": "2026-11-09T09:00:00+00:00",
  "finished_at": "2026-11-09T09:00:00+00:00",
  "id": "11111111-1111-1111-1111-111111111111",
  "job_type": "plan_trip",
  "last_error": "1",
  "lease_expires_at": "2026-11-09T09:00:00+00:00",
  "max_attempts": 1,
  "payload": {},
  "result": {},
  "started_at": "2026-11-09T09:00:00+00:00",
  "status": "PENDING",
  "updated_at": "2026-11-09T09:00:00+00:00"
}
"""
    static let localGuide = """
{
  "cities_covered": [
    "1"
  ],
  "countries": [
    {
      "cities": [
        {
          "city": "1",
          "tips": [
            "1"
          ]
        }
      ],
      "country_code": "1",
      "name": "1",
      "sections": [
        {
          "items": [
            "1"
          ],
          "key": "1",
          "title": "1"
        }
      ]
    }
  ],
  "data_gaps": [
    {
      "code": "1",
      "country_code": "1",
      "message": "1"
    }
  ],
  "trip_id": "11111111-1111-1111-1111-111111111111",
  "weather": [
    {
      "observed_at": "1",
      "place_id": "1",
      "place_name": "1",
      "precipitation_mm": 1.5,
      "temperature_c": 1.5,
      "weather_code": 1,
      "wind_kmh": 1.5
    }
  ]
}
"""
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
      "claim_status": "NEEDS_REVIEW",
      "claim_type": "FACT",
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
    static let suggestionFeed = """
{
  "daily_cap": {
    "cap": 1,
    "used": 1
  },
  "items": [
    {
      "confidence": 1.5,
      "created_at": "2026-11-09T09:00:00+00:00",
      "dedupe_key": "1",
      "evidence": [
        {
          "kind": "1",
          "ref": "1",
          "retrieved_at": "2026-11-09T09:00:00+00:00"
        }
      ],
      "id": "11111111-1111-1111-1111-111111111111",
      "proposed_action": {
        "kind": "1",
        "params": {},
        "tool": "1"
      },
      "rationale": "1",
      "requires_approval": true,
      "severity": "LOW",
      "status": "PENDING",
      "suggestion_type": "1",
      "title": "1",
      "trip_id": "11111111-1111-1111-1111-111111111111"
    }
  ],
  "next_cursor": "1"
}
"""
    static let syncSummary = """
{
  "applied": 1,
  "conflict": 1,
  "rejected": 1
}
"""
    static let travelerProfile = """
{
  "confidence": 1.5,
  "nationality": "1",
  "notes": "1",
  "preferences": {},
  "version": 1
}
"""
    static let trip = """
{
  "active_plan_version_id": "11111111-1111-1111-1111-111111111111",
  "budget_amount": "1",
  "countries": [
    "1"
  ],
  "created_at": "2026-11-09T09:00:00+00:00",
  "currency": "1",
  "customer_count": 1,
  "home_timezone": "1",
  "id": "11111111-1111-1111-1111-111111111111",
  "name": "1",
  "origin_place_id": "11111111-1111-1111-1111-111111111111",
  "owner_user_id": "11111111-1111-1111-1111-111111111111",
  "plan_version_count": 1,
  "purpose": "1",
  "status": "DRAFT",
  "updated_at": "2026-11-09T09:00:00+00:00",
  "version": 1,
  "visit_count": 1,
  "window_end": "2026-11-09T09:00:00+00:00",
  "window_start": "2026-11-09T09:00:00+00:00"
}
"""
    static let tripExpense = """
{
  "amount": "1",
  "category": "TRANSPORT",
  "created_at": "2026-11-09T09:00:00+00:00",
  "currency": "1",
  "id": "11111111-1111-1111-1111-111111111111",
  "note": "1",
  "occurred_on": "2026-11-09",
  "receipt_ref": "1",
  "trip_id": "11111111-1111-1111-1111-111111111111"
}
"""
    static let tripMemory = """
{
  "procedure": [
    {
      "attempt_id": "11111111-1111-1111-1111-111111111111",
      "created_at": "2026-11-09T09:00:00+00:00",
      "outcome": "1",
      "reason": "1"
    }
  ],
  "summaries": [
    {
      "confidence": 1.5,
      "decisions": [
        "1"
      ],
      "follow_ups": [
        "1"
      ],
      "id": "11111111-1111-1111-1111-111111111111",
      "kind": "1",
      "lessons": [
        "1"
      ],
      "summary": "1",
      "valid_from": "2026-11-09T09:00:00+00:00"
    }
  ],
  "trip_id": "11111111-1111-1111-1111-111111111111"
}
"""
    static let tripOutcome = """
{
  "actual_cost": [
    {
      "amount": 1.5,
      "count": 1,
      "currency": "1"
    }
  ],
  "insight_kind_labels": {
    "key": "1"
  },
  "insights_by_kind": {
    "key": 1
  },
  "memory_summaries": [
    {
      "kind": "1",
      "summary": "1"
    }
  ],
  "opportunity_signals": [
    {
      "kind": "1",
      "kind_label": "1",
      "requires_confirmation": true,
      "text": "1",
      "visit_id": "1"
    }
  ],
  "plan_cost": {
    "currency": "1",
    "total": 1.5,
    "unknown_count": 1
  },
  "record_outcome_labels": {
    "key": "1"
  },
  "record_outcomes": {
    "key": 1
  },
  "status": "1",
  "trip_id": "11111111-1111-1111-1111-111111111111",
  "visit_labels": {
    "key": "1"
  },
  "visits": {
    "key": 1
  },
  "window": {
    "key": "1"
  }
}
"""
    static let visaCase = """
{
  "checklist": [
    {
      "key": "1",
      "label": "1",
      "note": "1",
      "status": "MISSING"
    }
  ],
  "confidence": 1.5,
  "country_code": "1",
  "created_at": "2026-11-09T09:00:00+00:00",
  "decision_at": "2026-11-09T09:00:00+00:00",
  "id": "11111111-1111-1111-1111-111111111111",
  "notes": "1",
  "passport_expires_at": "2026-11-09",
  "status": "NEW",
  "submitted_at": "2026-11-09T09:00:00+00:00",
  "trip_id": "11111111-1111-1111-1111-111111111111",
  "updated_at": "2026-11-09T09:00:00+00:00"
}
"""
    static let visitBriefing = """
{
  "customer": {
    "country_code": "1",
    "external_ref": "1",
    "id": "11111111-1111-1111-1111-111111111111",
    "industry": "1",
    "name": "1",
    "notes": "1",
    "primary_contact_email": "1",
    "primary_contact_name": "1",
    "primary_contact_phone": "1",
    "status": "ACTIVE",
    "tier": "A"
  },
  "data_gaps": [
    {
      "code": "1",
      "message": "1"
    }
  ],
  "follow_ups": [
    {
      "due_date": "1",
      "title": "1",
      "trip_id": "11111111-1111-1111-1111-111111111111",
      "trip_name": "1",
      "visit_id": "11111111-1111-1111-1111-111111111111"
    }
  ],
  "history": [
    {
      "date": "2026-11-09",
      "notes": "1",
      "outcome": "1",
      "status": "PLANNED",
      "title": "1",
      "trip_id": "11111111-1111-1111-1111-111111111111",
      "trip_name": "1",
      "visit_id": "11111111-1111-1111-1111-111111111111"
    }
  ],
  "memories": [
    {
      "confidence": 1.5,
      "decisions": [
        "1"
      ],
      "follow_ups": [
        "1"
      ],
      "id": "11111111-1111-1111-1111-111111111111",
      "kind": "1",
      "lessons": [
        "1"
      ],
      "summary": "1",
      "valid_from": "2026-11-09T09:00:00+00:00"
    }
  ],
  "place": {
    "address_line": "1",
    "city": "1",
    "country_code": "1",
    "geocode_status": "1",
    "id": "11111111-1111-1111-1111-111111111111",
    "lat": 1.5,
    "lon": 1.5,
    "name": "1",
    "region": "1",
    "timezone": "1"
  },
  "visit": {
    "business_value": "1",
    "client_updated_at": "2026-11-09T09:00:00+00:00",
    "created_at": "2026-11-09T09:00:00+00:00",
    "customer_id": "11111111-1111-1111-1111-111111111111",
    "id": "11111111-1111-1111-1111-111111111111",
    "is_immovable": true,
    "movement_mode": "GROUND",
    "objective": "1",
    "place_id": "11111111-1111-1111-1111-111111111111",
    "priority": 1,
    "service_minutes": 1,
    "status": "PLANNED",
    "time_window_end": "2026-11-09T09:00:00+00:00",
    "time_window_start": "2026-11-09T09:00:00+00:00",
    "title": "1",
    "trip_id": "11111111-1111-1111-1111-111111111111",
    "updated_at": "2026-11-09T09:00:00+00:00"
  }
}
"""
    static let visitRecord = """
{
  "applied": true,
  "client_record_id": "1",
  "conflict": {
    "client": {},
    "reason": "1",
    "server": {}
  },
  "created_at": "2026-11-09T09:00:00+00:00",
  "id": "11111111-1111-1111-1111-111111111111",
  "outcome": "MET",
  "server_version": 1,
  "visit_id": "11111111-1111-1111-1111-111111111111"
}
"""
}
