# ADR-0046: Assistant tool result deep links and retry-stable idempotency

- Status: Accepted
- Date: 2026-11-25
- Scope: iOS Assistant tool execution result navigation and client retry identity

## Context

Agent tool execution is an explicit user action backed by a server-side 24-hour idempotency record. Two client gaps remained:

- A visa tool result navigated to the Trip overview rather than the visa section it had just prepared or read.
- Each new tap generated a new idempotency key. If the API committed but the response was lost, a manual retry could create a second AgentRun and AssistantMessage even though the underlying VisaCase creation was conflict-safe.

## Decision

1. Tool result cards carry a destination hint: `trip_plan` for `trip.get_plan`, `trip_visa` for visa tools, and the existing `trip` link for generic Trip results.
2. `TripDetailView` accepts an optional initial section. Existing callers keep the default overview; Assistant result cards select the relevant plan or visa section.
3. The iOS Assistant stores a generated idempotency token and creation time in UserDefaults under a key scoped to conversation, tool and trip.
4. The same token is reused after uncertain network failures and app relaunch. It is removed only after the tool endpoint returns successfully.
5. The client reuses the token for the full 24-hour server idempotency retention window; a later tap is treated as a new invocation.
6. The server continues to bind the idempotency fingerprint to the acting user. No server task model or schema migration is introduced.

## Consequences

- Users arrive directly at the authoritative Trip section corresponding to the Agent result.
- A lost response followed by a user retry replays the first successful response instead of creating a duplicate AgentRun/AssistantMessage.
- A deliberate future execution after a successful response receives a fresh idempotency key.
