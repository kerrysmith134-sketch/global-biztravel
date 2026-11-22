# Sprint H2.3 — Native iOS Market Research Workspace

> Base: `feat/sprint-h2.2-question-evidence-scope`  
> Scope: native iOS workspace / DAG / question research / human review UI. No new backend execution semantics.

## Product intent

Expose the H2.1 durable DAG and H2.2 per-question evidence loop in the app. A user can move from a Workspace research question to reviewed external sources and a human-approved answer without leaving the native client.

## Navigation

- Entry is a “研究工作区” action in Today > More. Do not add a sixth Tab.
- Workspace list/create.
- Workspace detail: objective, question graph status, execution controls, and question list.
- Question detail: ResearchPlan, editable search queries, retrieval status, evidence sources, answer versions and human review.

## Interaction contract

1. A user creates/selects a Workspace and adds or opens a Question.
2. “生成研究计划” creates a ResearchPlan with `workspace_id` + `target_question_id`; the server's planner proposes queries but performs no search.
3. The user can edit the generated queries. Only the exact non-empty list submitted from the confirmation action is sent to `POST ...:execute`.
4. The UI shows queued/running/complete/failed accurately. Search snippets remain UNVERIFIED and open their source URL through the system browser.
5. “生成答案草稿” enqueues the existing evidence-backed Answer Agent. Answer drafts do not advance the DAG.
6. The user explicitly accepts/rejects each answer version. Only accepted answers release dependencies.
7. The Workspace view can start/inspect/cancel the durable graph execution. The UI reflects the server's node states, never locally infers completion.

## Architecture

- Extend existing Research domain models and `ResearchRepository`; use the shared `APIClient`, `Endpoints`, `DateCoding` and idempotency conventions.
- Add wire models for MarketWorkspace, ResearchQuestion, ResearchGraph, DAG execution and QuestionAnswer.
- `AppEnvironment.researchRepository` remains the single injection point; no parallel client stack or local fake Agent.
- SwiftUI presentation reuses DSCard, DSBadge, DSSectionHeader, DSDataQualityTag, spacing, colours and motion tokens.
- Workspace Research is network-backed because its evidence and execution state are server-owned. No offline fallback may fabricate search or answer states.

## Visual / UX constraints

- Keep the page scannable: node state, dependency wait, evidence readiness, retrieval state and answer review are compact metadata.
- Long planner prose, query editing and answer details are progressive disclosure, not one giant text page.
- Evidence cards display source host/title/date and an explicit UNVERIFIED label. A source with no usable URL stays non-clickable.
- Disable duplicate submit while loading; keep error messages local to the action; retain user-edited queries after transient failures.
- Destructive cancel/reject actions require a confirmation or explicit reason; accept/reject writes carry fresh idempotency keys.

## Acceptance tests

- Swift model decoding for nullable target_question_id, Workspace, Question, Graph, Execution and Answer payloads.
- Endpoint construction and snake_case body contract for create Workspace/Question, plan target ID, query execute, DAG execution and answer review.
- SwiftLint, Xcode build, XCTest and existing UI tests green.
- No navigation regressions, no added Tab, no emoji-as-icons, and no hardcoded fake success state.
