# ADR-0039: Shared Agent execution lifecycle

- Status: Accepted
- Date: 2026-11-25
- Scope: backend AgentRun persistence lifecycle; no schema migration

## Context

The repository already has distinct persistence primitives with distinct responsibilities:

- WorkflowInstance represents a business workflow and owns its domain-level status.
- BackgroundJob represents durable scheduling, retries, leases and worker recovery.
- AgentRun represents one bounded Agent execution and its input/output audit.
- Evidence records provenance for facts and source material.
- ExternalAction records externally consequential actions and their approval state.
- Action Center remains a read model over authoritative business records; it is not a second task table.

Research planning and versioned context extraction both created and finalized AgentRun directly. That duplicated persistence rules and allowed lifecycle semantics to drift.

## Decision

1. Centralize AgentRun creation and terminal transitions in agent_runtime_service.
2. Creation requires an existing parent workflow in the same organization when workflow_id is supplied. Only ACCEPTED or RUNNING workflows can start a new run.
3. Every run begins as RUNNING; terminal transitions are one-way to SUCCEEDED or FAILED. Re-finalization is rejected.
4. Finish timestamps must be timezone-aware. Error diagnostics are bounded to 2,000 characters.
5. Callers continue to own their business WorkflowInstance transition, task scheduling, idempotency, evidence provenance, approvals, and domain-specific output shape.
6. AgentRun input must be minimized by each caller. Persist references, prompt versions, content hashes and character counts rather than raw documents or pasted source text.
7. No second task table, generic action queue, or automatic tool execution is introduced.

## Consequences

- Research planning and context extraction share one tested lifecycle contract without changing their external API or database schema.
- A future Agent can reuse the same primitive while keeping its workflow graph and background scheduling explicit.
- This is a foundation, not a claim that the entire app already has a unified Agent planner, policy engine, tool dispatcher, approval inbox or long-running agent supervisor. Those remain separate implementation increments and must reuse the existing authorization and business services.
