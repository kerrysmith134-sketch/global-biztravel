# ADR-0043: First executable read-only Agent tool

- Status: Accepted
- Date: 2026-11-25
- Scope: Assistant tool execution for `trip.get_plan`; no schema migration

## Context

The tool catalogue described several tools with handler identifiers, but only the catalogue existed. Assistant messages could propose tools by keyword, while the iOS client had no execution action for those proposal cards. A catalogue entry is not proof that a handler, authorization rule, approval path, or result projection exists.

## Decision

1. A tool is executable only when it has an explicit handler registered in the server-side executor map.
2. The first vertical slice is read-only `trip.get_plan`, scoped to the trip bound to the Assistant conversation.
3. The tool service validates arguments with a strict Pydantic model, checks the declared `resource:action` scope against the caller's role, and reuses tenant-scoped trip and plan read services.
4. No active plan is returned as an explicit `NO_ACTIVE_PLAN` result; the tool must never fabricate a plan or silently substitute a draft.
5. Every accepted execution creates an `AgentRun`; its input snapshot contains only the tool name and stable trip reference, and its output snapshot records bounded result metadata.
6. The API endpoint uses the existing transactional `IdempotentWrite` guard. Replaying the same key must not create a second AssistantMessage or AgentRun.
7. Tool proposal cards are shown only for tools with concrete handlers. Approval-gated catalogue entries remain non-executable until they are connected to the existing approval/external-action workflow.
8. The iOS tool button calls this endpoint and refreshes the conversation. The result card links to the trip detail screen for the full itinerary.

## Consequences

- A user can request a plan from the Assistant and then explicitly execute the read-only tool; the app returns a truthful state and a navigable result.
- New tools must add a concrete handler, strict input model, scope, output/audit contract, tests, and (where consequential) a real approval workflow before becoming discoverable.
- This is one executable tool, not a claim that model-driven function calling or the full catalogue is complete.
