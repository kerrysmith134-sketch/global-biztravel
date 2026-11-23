# Sprint H2.6 — Source Verification Planner

## Scope

H2.6 adds a deterministic, read-only source-verification planner over the existing Workspace Evidence graph and immutable Claim–Evidence snapshots.

Endpoint:

`GET /v1/market/workspaces/{workspace_id}/source-verification-plan`

No new evidence store, database table, migration, or background job is introduced. The planner uses the current tenant and Workspace scope and returns bounded recommendations.

## What it detects

- Search-result records that have no extracted page-text child.
- Exact content fingerprints repeated across different hosts (possible syndication or reuse).
- Different content fingerprints under one canonical source URL (source-version drift).
- Claim evidence whose current stored hash differs from the immutable claim snapshot.
- A newer, changed Evidence version under the same canonical URL after a claim was assessed.
- Verification-status changes after a claim assessment.
- Claims with support and contradiction links, weak source-origin patterns, or only one support source.

## Source-origin policy

The planner distinguishes one source, same-host sources, possible cross-host syndication, and multiple hosts with different observed content. The last state is still **unconfirmed**. Distinct domains and distinct content hashes do not establish different publishers, ownership, editorial independence, or primary-source status. `confirmed_independent_source_count` remains human-governed and is never raised by this planner.

## Recommendations

The plan may recommend extracting a user-selected search result, completing source metadata, investigating possible syndication, reviewing source-version drift, re-verifying a claim after its evidence changes, handling contradictions, or finding separately originated corroboration.

These are review tasks, not executable commands. This endpoint does not fetch pages, alter Evidence, mutate claims, rerun LLM verification, or accept a claim.

## Limits and safety

- At most 500 recent Evidence rows are analyzed for Workspace-wide source tasks.
- At most 200 non-superseded claims are analyzed.
- At most 250 actions are returned; truncation is explicit.
- Claim-linked Evidence is revalidated against the current organization, Workspace, and linked ResearchPlan scope.
- The plan is read-only and emits caveats about source independence and ownership.
- No freshness cutoff is invented; publication dates and retrieval timestamps remain contextual metadata.
