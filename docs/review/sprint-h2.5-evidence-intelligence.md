# Sprint H2.5 — Evidence Intelligence Report

> Base: \`feat/sprint-h2.4-context-extraction-agent\`  
> Scope: deterministic, read-only diagnostics over existing Workspace / ResearchPlan Evidence. No new evidence store and no automated truth certification.

## Delivered contract

- Evidence is analyzed inside one organization and one Workspace, including Evidence attached to that Workspace's linked ResearchPlans.
- Exact content fingerprints identify duplicate-content candidates across URLs.
- Exact canonical references with multiple distinct fingerprints identify source-version drift.
- Exact hostname grouping highlights same-host material; hostnames are not publisher identities.
- Source extraction derivations identify search results that have not yet produced a page-text child.
- Verification status, missing publication date, missing stored hash, unresolved host and future publication timestamps are surfaced as explicit issue codes.
- Publication age is informational. The report never declares a source stale from a global threshold.
- The report is read-only and recomputed on request. It analyzes at most 500 most-recent Evidence records; truncation is explicit.

## API

\`GET /v1/market/workspaces/{workspace_id}/evidence-intelligence\`

Viewer role. The result contains summary counts, per-Evidence diagnostics and non-negotiable caveats. No schema migration is required.

## Interpretation safeguards

- Distinct hosts do not prove independent publishers, ownership separation or absence of syndication.
- Equal content fingerprints identify exact-content duplicate candidates, not the original publisher.
- \`BODY_SHA256\` is a fallback fingerprint computed over the stored body; it is not interchangeable with a source-provided full-page hash.
- Freshness must be determined by the user's research question and domain-specific time sensitivity, not a universal age threshold.
- Truncated reports only describe relationships among the analyzed subset.

## Acceptance

PostgreSQL tests cover duplicate content across hosts, same-source multi-version detection, missing metadata, unextracted search results and tenant isolation. iOS exposes the report's summary and per-source issues without hiding its caveats.
