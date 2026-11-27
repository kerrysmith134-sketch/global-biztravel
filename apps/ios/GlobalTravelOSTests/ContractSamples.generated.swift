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
      "receipt_asset_id": "11111111-1111-1111-1111-111111111111",
      "receipt_byte_length": 1,
      "receipt_mime_type": "1",
      "receipt_original_filename": "1",
      "receipt_ref": "1",
      "receipt_sha256": "1",
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
    static let marketClaimImpactReport = """
{
  "affected_consumer_count": 1,
  "analyzed_lineage_count": 1,
  "generated_at": "2026-11-09T09:00:00+00:00",
  "impact_count": 1,
  "impacts": [
    {
      "claim_lineage_id": "11111111-1111-1111-1111-111111111111",
      "consumer_id": "11111111-1111-1111-1111-111111111111",
      "consumer_status": "1",
      "consumer_title": "1",
      "consumer_type": "INSIGHT",
      "current_accepted_claim_id": "11111111-1111-1111-1111-111111111111",
      "latest_claim_id": "11111111-1111-1111-1111-111111111111",
      "latest_claim_status": "NEEDS_REVIEW",
      "latest_claim_version": 1,
      "latest_verification_verdict": "PROVISIONAL_SUPPORT",
      "matched_evidence_count": 1,
      "reason_code": "LATEST_VERSION_CONTRADICTORY",
      "referenced_claim_versions": [
        1
      ],
      "relation_types": [
        "SUPPORTS"
      ],
      "severity": "HIGH",
      "stale_evidence_count": 1
    }
  ],
  "total_lineage_count": 1,
  "truncated": true,
  "workspace_id": "11111111-1111-1111-1111-111111111111"
}
"""
    static let marketClaimLineageHistory = """
{
  "claim_key": "1",
  "claim_lineage_id": "11111111-1111-1111-1111-111111111111",
  "current_accepted_claim_id": "11111111-1111-1111-1111-111111111111",
  "latest_version": 1,
  "versions": [
    {
      "answer_id": "11111111-1111-1111-1111-111111111111",
      "claim_key": "1",
      "claim_lineage_id": "11111111-1111-1111-1111-111111111111",
      "claim_type": "FACT",
      "confidence_level": "MEDIUM",
      "confirmed_independent_source_count": 1,
      "contradicting_evidence_count": 1,
      "created_at": "2026-11-09T09:00:00+00:00",
      "distinct_host_count": 1,
      "evidence": [
        {
          "content_hash_snapshot": "1",
          "evidence": {
            "canonical_ref": "1",
            "content_hash": "1",
            "created_at": "2026-11-09T09:00:00+00:00",
            "id": "11111111-1111-1111-1111-111111111111",
            "kind": "1",
            "observation": "1",
            "source": "1",
            "verification_status": "1"
          },
          "evidence_id": "11111111-1111-1111-1111-111111111111",
          "origin_assessment": "SINGLE_SOURCE",
          "rationale": "1",
          "relation_type": "SUPPORTS",
          "source_host": "1",
          "verification_status_snapshot": "1"
        }
      ],
      "id": "11111111-1111-1111-1111-111111111111",
      "limitations": [
        "1"
      ],
      "reasoning": "1",
      "review_note": "1",
      "reviewed_at": "2026-11-09T09:00:00+00:00",
      "reviewed_by_user_id": "11111111-1111-1111-1111-111111111111",
      "scope": {},
      "statement": "1",
      "status": "NEEDS_REVIEW",
      "supersedes_id": "11111111-1111-1111-1111-111111111111",
      "supporting_evidence_count": 1,
      "verification_run_id": "11111111-1111-1111-1111-111111111111",
      "verification_verdict": "PROVISIONAL_SUPPORT",
      "verified_contradiction_count": 1,
      "verified_support_count": 1,
      "version": 1,
      "workspace_id": "11111111-1111-1111-1111-111111111111"
    }
  ],
  "workspace_id": "11111111-1111-1111-1111-111111111111"
}
"""
}
