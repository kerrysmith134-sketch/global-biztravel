// 自动生成，请勿手改：scripts/gen_ios_contract_samples.py
// ContractSamples 分片；与主样本文件由同一 OpenAPI schema 生成。

extension ContractSamples {
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
  "receipt_asset_id": "11111111-1111-1111-1111-111111111111",
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
    static let tripRiskCase = """
{
  "acknowledged_at": "2026-11-09T09:00:00+00:00",
  "case_key": "1",
  "context": {},
  "created_at": "2026-11-09T09:00:00+00:00",
  "dismissed_at": "2026-11-09T09:00:00+00:00",
  "escalated_at": "2026-11-09T09:00:00+00:00",
  "evidence_refs": [
    {}
  ],
  "first_seen_at": "2026-11-09T09:00:00+00:00",
  "id": "11111111-1111-1111-1111-111111111111",
  "last_observed_at": "2026-11-09T09:00:00+00:00",
  "next_review_at": "2026-11-09T09:00:00+00:00",
  "organization_id": "11111111-1111-1111-1111-111111111111",
  "resolution_basis": "1",
  "resolution_note": "1",
  "resolved_at": "2026-11-09T09:00:00+00:00",
  "risk_type": "1",
  "severity": "LOW",
  "source_refs": [
    {}
  ],
  "started_at": "2026-11-09T09:00:00+00:00",
  "status": "OPEN",
  "summary": "1",
  "title": "1",
  "trip_id": "11111111-1111-1111-1111-111111111111",
  "updated_at": "2026-11-09T09:00:00+00:00",
  "version": 1,
  "waiting_since": "2026-11-09T09:00:00+00:00"
}
"""
    static let tripRiskCaseEvent = """
{
  "actor_type": "SYSTEM",
  "actor_user_id": "11111111-1111-1111-1111-111111111111",
  "case_id": "11111111-1111-1111-1111-111111111111",
  "case_version": 1,
  "created_at": "2026-11-09T09:00:00+00:00",
  "detail": {},
  "event_type": "1",
  "from_status": "OPEN",
  "id": "11111111-1111-1111-1111-111111111111",
  "note": "1",
  "organization_id": "11111111-1111-1111-1111-111111111111",
  "to_status": "OPEN",
  "trip_id": "11111111-1111-1111-1111-111111111111"
}
"""
    static let tripRiskReconciliation = """
{
  "cases_created": 1,
  "cases_reopened": 1,
  "cases_updated": 1,
  "errors": [
    "1"
  ],
  "escalated": 1,
  "reviewed": 1,
  "trip_id": "11111111-1111-1111-1111-111111111111"
}
"""
    static let tripRiskTransition = """
{
  "case": {
    "acknowledged_at": "2026-11-09T09:00:00+00:00",
    "case_key": "1",
    "context": {},
    "created_at": "2026-11-09T09:00:00+00:00",
    "dismissed_at": "2026-11-09T09:00:00+00:00",
    "escalated_at": "2026-11-09T09:00:00+00:00",
    "evidence_refs": [
      {}
    ],
    "first_seen_at": "2026-11-09T09:00:00+00:00",
    "id": "11111111-1111-1111-1111-111111111111",
    "last_observed_at": "2026-11-09T09:00:00+00:00",
    "next_review_at": "2026-11-09T09:00:00+00:00",
    "organization_id": "11111111-1111-1111-1111-111111111111",
    "resolution_basis": "1",
    "resolution_note": "1",
    "resolved_at": "2026-11-09T09:00:00+00:00",
    "risk_type": "1",
    "severity": "LOW",
    "source_refs": [
      {}
    ],
    "started_at": "2026-11-09T09:00:00+00:00",
    "status": "OPEN",
    "summary": "1",
    "title": "1",
    "trip_id": "11111111-1111-1111-1111-111111111111",
    "updated_at": "2026-11-09T09:00:00+00:00",
    "version": 1,
    "waiting_since": "2026-11-09T09:00:00+00:00"
  },
  "event": {
    "actor_type": "SYSTEM",
    "actor_user_id": "11111111-1111-1111-1111-111111111111",
    "case_id": "11111111-1111-1111-1111-111111111111",
    "case_version": 1,
    "created_at": "2026-11-09T09:00:00+00:00",
    "detail": {},
    "event_type": "1",
    "from_status": "OPEN",
    "id": "11111111-1111-1111-1111-111111111111",
    "note": "1",
    "organization_id": "11111111-1111-1111-1111-111111111111",
    "to_status": "OPEN",
    "trip_id": "11111111-1111-1111-1111-111111111111"
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
}
