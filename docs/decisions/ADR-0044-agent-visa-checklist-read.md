# ADR-0044: Read-only Agent access to existing visa checklist records

- Status: Accepted
- Date: 2026-11-25
- Scope: Assistant tool `visa.get_checklist`; no schema migration

## Context

The product already has authoritative `VisaCase` records and checklist state, created and updated through the visa workflow. The catalogue also contains `visa.prepare_checklist`, which is approval-gated and has no connected executor.

An Agent must not create visa cases, infer official entry rules, or silently turn an offline catalogue entry into a visa decision merely because a user asks to "check my visa".

## Decision

1. Introduce a separate read-only `visa.get_checklist` tool with `trip:read` scope and no approval requirement.
2. Its executor reads only existing tenant-scoped `VisaCase` records via `visa_service.list_cases`; it never calls `ensure_cases`, edits checklist values, or refreshes entry assessments.
3. If no cases exist, return `NO_VISA_CASES` and a truthful explanation. Do not synthesize checklist items.
4. Summaries count persisted `READY`, `MISSING`, and `NOT_APPLICABLE` values and report case statuses by destination country.
5. The tool records minimized trip-reference input and aggregated result counts in `AgentRun`; no passport details or raw notes are copied into the run snapshot.
6. Assistant keyword proposals are displayed only when the tool has a concrete handler. The iOS action opens the existing trip detail screen after the result card; its 签证与入境 section remains the authoritative create/update surface.
7. `visa.prepare_checklist` remains blocked until its actual approval semantics are connected.

## Consequences

- The Assistant can report the traveller's recorded checklist progress without making unsupported visa/legal claims.
- All create/update/status transitions continue through the existing visa business service and API.
- No additional schema, task table, or shadow checklist model is introduced.
