# ADR-0047: Model-assisted tool proposals with server policy filtering

- Status: Accepted
- Date: 2026-11-25
- Scope: Assistant reply schema and tool proposal generation; no execution-path changes

## Context

The Assistant previously proposed tools only through keyword matching in `_facts()`. The LLM generated the natural-language reply but could not help select a relevant tool from the executable catalogue. This limited intent coverage and made the Assistant feel less agentic.

## Decision

1. Add an optional structured `tool_suggestions` array to Assistant LLM output and bump the prompt version to `v2` so cached v1 completions cannot be mistaken for the new contract.
2. Pass only the currently executable tool catalogue (name, description, scope) for the conversation's bound trip into the prompt.
3. Treat model output as untrusted candidate names. The server drops non-strings, unknown names, tools without a concrete executor, and any catalogue item that requires a missing approval workflow.
4. Keep deterministic policy suggestions as a fallback, so degraded/no-provider mode still exposes known keyword-supported tools.
5. A write-scoped tool is proposed only when the user's message contains explicit create/prepare intent. The user must still tap the proposal card; model output never invokes a handler.
6. The tool executor continues to re-check organization, conversation owner, bound trip, resource scope and role at execution time. This ADR changes suggestion quality, not authorization or execution semantics.

## Consequences

- The LLM participates in tool selection while all executable authority remains server-side.
- A model hallucinating an unregistered tool name cannot create a card or reach the executor.
- A model cannot turn a read request into a write-tool proposal without the explicit intent gate.
- No database schema or API request/response contract changes are introduced; AssistantMessage cards remain the existing UI contract.
