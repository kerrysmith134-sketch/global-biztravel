# Sprint H2.4 — Market Context Extraction Agent

> Base: \`feat/sprint-h2.3-ios-research-workspace\`  
> Scope: grounded extraction for an existing immutable \`MarketContextSnapshot\`; no new context store, job runtime, or industry taxonomy.

## Product contract

- A user explicitly triggers extraction for a selected Workspace context version.
- The agent organizes only that version's user-provided \`raw_input\`; it does not search the web or read unrelated Trips / Customers.
- Each extracted category item must carry an exact source quote found in the original input. Items without a matching quote are discarded and surfaced as an extraction gap.
- Industry, offering, business-model, customer-segment and market labels remain dynamic text.
- \`user_overrides\` always win over model extraction. The model cannot rewrite the original input or a user's confirmed override.
- The model call uses the shared LLM Gateway, prompt registry, LLMInvocation audit and existing WorkflowInstance / AgentRun records.
- AgentRun input stores only the context reference, version, content hash and character count; raw context text is not copied into the run payload.
- A degraded Gateway response marks the snapshot and workflow FAILED. Empty fallback output is never marked as a successful extraction.
- A completed extraction for the same prompt version is idempotent, including requests made with a new HTTP idempotency key.

## API

\`POST /v1/market/workspaces/{workspace_id}/contexts/{context_id}:extract\`

The endpoint uses the existing traveler role, organization scoping and IdempotentWrite mechanism and returns the updated \`MarketContextRead\`. No migration is required: the snapshot already has extraction status, prompt version, provenance, extracted context and unknowns fields.

## Verification

- Exact-quote grounding rejects a model-invented source quote.
- User overrides survive extraction and are marked \`USER_EDIT\`.
- Model-derived fields retain prompt and input-hash provenance.
- Raw input is not persisted in AgentRun input.
- A degraded model response does not create a successful context.
- Existing context versioning and adoption semantics remain unchanged.

## Non-goals

No market facts, market sizing, customer discovery or external research are produced by this agent. Those belong to the user-confirmed research-plan and Evidence workflows already implemented in H2.2 / H2.3.
