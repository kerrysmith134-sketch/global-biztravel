# ADR-0050: Verify Assistant tool execution through the iOS UI

- Status: Accepted
- Date: 2026-11-25
- Scope: Real-services iOS UI acceptance coverage for the Assistant `trip.get_plan` tool

## Context

Backend integration tests verified proposal filtering, explicit tool execution, tenant and actor authorization, AgentRun auditing, and idempotent replay. The iOS client also had unit tests for retry-key durability. However, the real-services UI suite did not exercise the full user journey from a trip detail page into Assistant, tapping a tool proposal, and following the resulting business deep link.

## Decision

1. Add stable accessibility labels/identifiers for the Assistant new-conversation, message-input, and send controls.
2. Add an iOS UI acceptance test that creates a real trip with no active plan through the test API.
3. Navigate from trip detail into the Assistant, request the current plan, and use the deterministic policy proposal produced by the local Stub provider.
4. Tap the actual `trip.get_plan` proposal card and assert that the server truthfully returns `NO_ACTIVE_PLAN`.
5. Open the result card and verify navigation to the plan section, including the no-plan empty state.
6. Run this test only in the existing real PostgreSQL + API + Worker acceptance suite; no mocked repositories or fabricated tool response.

## Consequences

- The first executable Agent tool now has an end-to-end iOS acceptance path, not just backend/API tests.
- Accessibility identifiers are stable, descriptive selectors for both automated acceptance and future assistive technology support.
- This test does not depend on an external LLM provider or API key; the local Stub provider plus deterministic server policy path makes the proposal reproducible.
