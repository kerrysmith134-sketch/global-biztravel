# Sprint H2.9 — Claim Impact Intelligence

## Goal

Close the gap between cross-answer Claim lineage (H2.8) and existing Workspace business conclusions. When a Claim is re-verified, show which Market Insights and Opportunities still cite Evidence snapshots attached to that lineage.

## Deterministic impact rules

The report joins only explicit Evidence IDs already persisted in:

- `MarketClaimEvidence`
- `MarketInsightEvidence`
- `MarketOpportunityEvidence`

It does not infer dependencies from similar wording, shared company names, or LLM-generated explanations.

A consumer is included when:

- the latest Claim version is awaiting review, conflicted, or insufficient and the consumer references Evidence attached to that Claim lineage; or
- a newer Claim version has been accepted, while the consumer still references one or more older Evidence IDs that are absent from the latest Claim version; or
- the latest Claim is rejected and no accepted version remains, while the consumer still references evidence from the lineage.

Rejected/superseded Insights and terminal Opportunity states (DISQUALIFIED, WON, LOST) are excluded from the active impact list.

## API

- `GET /v1/market/workspaces/{workspace_id}/claim-impact-report`

The response contains lineage and latest-claim identifiers, current accepted Claim pointer, affected consumer type/ID/title/state, relation types, matched and stale Evidence counts, referenced Claim versions, severity and deterministic reason code.

## Safety and scope

- Read-only endpoint; no schema migration.
- No automatic edits to Insight, Opportunity, qualification, outcomes, Claim status or Evidence.
- Tenant and Workspace filters are applied to all reads.
- Caps: 200 lineages, 5000 Claim history rows, 250 returned impacts. Any cap sets `truncated=true`.
- Shared Evidence IDs prove an explicit data linkage, not semantic causality or that a consumer conclusion is necessarily wrong.
- This phase intentionally does not create polymorphic Claim-to-Consumer dependency tables. Those require a separate product decision on explicit linkage and human resolution semantics.

## iOS

Workspace detail shows a compact Business Conclusion Impact panel with affected-consumer/relationship metrics, reason, latest Claim version and stale Evidence count. The report refreshes with Workspace reload and does not introduce a second task queue.

## Validation

PostgreSQL integration coverage creates one accepted Claim, an Insight and an Opportunity citing its Evidence, then generates a second Claim version with a new source. It verifies the pending-review impact and, after human acceptance of version 2, the stale-Evidence snapshot signal. iOS endpoint/model tests and generated OpenAPI contract samples cover decoding.
