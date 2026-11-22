# Sprint H2.7 — Evidence-to-Research Loop

## Goal

Turn the deterministic source-verification actions from H2.6 into durable, auditable questions in the existing Workspace Research DAG. This closes the gap between "the system found a problem" and "the system can organize the work to investigate it".

## User flow

1. Read the current source-verification plan.
2. Explicitly choose **Apply to Research Graph**.
3. The server creates Agent-generated Research Questions, preserving the action ID, plan digest, evidence IDs, user initiator and AgentRun provenance.
4. Claim-level tasks depend on source-level tasks that touch the same Evidence IDs. The existing DAG scheduler therefore does not dispatch a claim re-verification before its source tasks are completed and manually accepted.
5. The user explicitly starts the existing Research DAG execution. Applying a plan never dispatches paid model work.
6. Research answers remain in the existing answer-review workflow. The Agent cannot accept an answer or a claim.
7. Returning to the Workspace reloads the source-verification plan and shows the linked Research Question state. The plan is always recomputed from current Evidence and Claim snapshots; it does not mark an action resolved merely because a question exists.

## API

- `POST /v1/market/workspaces/{workspace_id}/source-verification-plan:apply`

The POST is protected by the existing traveler role and Idempotency-Key mechanism. In addition, the service computes a stable semantic plan digest (excluding timestamps and display-only task status) and reuses a previous application when the action set has not changed.

The operation returns created/reused question counts, dependency count, action-to-question mappings and whether the application itself was reused.

## DAG policy

- Source-level Evidence actions become upstream research questions.
- Claim-level actions depend on upstream source questions when their Evidence ID sets overlap.
- Existing cycle checks are reused for every dependency edge.
- The operation is rejected while a Research DAG execution is running, because an active execution owns an immutable graph snapshot.
- No migration or new queue is added. The existing WorkflowInstance, AgentRun, MarketWorkspaceWorkflow, MarketResearchQuestion and MarketQuestionDependency records are reused.

## Human approval boundary

This operation does not:
- run the Research DAG automatically;
- fetch a webpage or mutate Evidence;
- accept a generated answer;
- create, accept or reject a MarketClaim;
- confirm source independence or publisher ownership.

After applying tasks, the user explicitly starts the existing DAG execution. Answer drafting and answer acceptance continue to use the existing queue and human review flow. The refreshed verification plan reports which action is linked to an OPEN, IN_PROGRESS, ANSWERED, BLOCKED or CANCELLED question; it still derives unresolved actions from the current evidence state rather than equating an answered question with a verified claim.

## Validation

PostgreSQL integration coverage verifies conversion of evidence actions to Research Questions, graph node creation, semantic idempotency across different HTTP idempotency keys, reloading task links, claim-task dependencies on source-level questions, and the no-automatic-dispatch boundary. iOS coverage verifies the apply endpoint, response decoding and the Workspace action.
