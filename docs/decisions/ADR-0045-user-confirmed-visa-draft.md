# ADR-0045: User-confirmed creation of local visa checklist drafts

- Status: Accepted
- Date: 2026-11-25
- Scope: Assistant tool `visa.prepare_checklist`; no schema migration

## Context

The visa domain already supports local `VisaCase` creation with default checklist items through `visa_service.ensure_cases`. It is a reversible preparation record: it does not submit an application, contact an embassy, or assert a visa decision. The unique key `(organization_id, trip_id, country_code)` and insert-on-conflict behavior make repeated preparation safe.

## Decision

1. Expose `visa.prepare_checklist` only as a proposed tool card; it is never auto-executed from message generation.
2. The user must explicitly tap “创建签证清单” before the tool endpoint is called.
3. The handler requires `trip:write` / `TRAVELER` and the current conversation owner, and reuses `visa_service.ensure_cases` with the acting user ID.
4. The tool is marked as not requiring a separate approval-policy workflow because the only side effect is creating local draft cases after an explicit user tap. External submission, payment, or official filing remains out of scope and must use a separate approval-gated action.
5. Tool execution is idempotent at the request boundary and the domain layer remains unique/upsert-safe. The AgentRun input snapshot retains the trip ID and normalized country list; output retains only aggregate counts.
6. If no destinations are available, return `NO_DESTINATIONS`; do not invent a country or create a global checklist.
7. An explicitly supplied country subset must be contained in the Trip's registered destination countries; out-of-trip country codes fail closed before `ensure_cases` is called.
8. Write-scoped tool execution requires an acting user identity; missing actor identity is rejected before AgentRun creation so local draft records always retain creator provenance.
9. Existing visa APIs remain the authoritative edit/status surface. The tool never updates checklist item status or refreshes entry assessments.

## Consequences

- The Assistant can prepare a local visa checklist through a clear user-confirmed action.
- Replaying a request cannot create duplicate cases or duplicate AgentRun/AssistantMessage records.
- No schema migration, shadow checklist model, or external submission integration is introduced.
