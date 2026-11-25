# ADR-0048: Conservative intent gate for checklist creation proposals

- Status: Accepted
- Date: 2026-11-25
- Scope: Model-assisted proposal filtering for `visa.prepare_checklist`; no API or database migration

## Context

The previous write-intent matcher treated broad phrases such as “准备签证材料” and “prepare visa documents” as explicit requests to create a checklist. Those phrases often occur in informational questions about requirements or process, so they could produce an unexpected write-capable proposal card.

## Decision

1. Only tool-specific phrases that clearly ask to create, generate, establish, or prepare a checklist/case qualify as positive intent, including an explicit request to create a visa case.
2. Generic questions about documents, requirements, process, and how-to steps must not propose the checklist-creation tool, even when they contain words such as “create”.
3. Existing negation filtering and a dedicated information-request phrase gate both fail closed.
4. Read tools and write tools continue to use the same executor allow-list; this gate only controls whether a write proposal card is shown.
5. Extend the LLM gateway's bounded schema validator to validate array item types, and declare `tool_suggestions` as an array of strings.

## Consequences

- More precise write proposal suggestions with a conservative false-negative bias rather than surprising users with creation prompts.
- Malformed LLM candidate arrays fail the output schema and use the configured fallback; the server-side allow-list remains the final control.
- No change to execution authorization, user confirmation, tool handlers, or persistence schema.
