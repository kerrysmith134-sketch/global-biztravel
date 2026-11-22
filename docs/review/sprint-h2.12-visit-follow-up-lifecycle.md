# H2.12 — Visit Follow-up Lifecycle

## Objective

Close the gap between visit observations and actual follow-through. The app already captures on-site notes and can draft meeting minutes; H2.12 turns their proposed follow-ups into accountable work only after an explicit human decision.

This is a visit-domain capability, not a second general-purpose task manager. The visit remains the source of truth and the existing Action Center is the cross-trip aggregation surface.

## User flow

1. A traveler saves an applied VisitRecord or generates a successful meeting-minutes draft.
2. The workspace presents follow-up suggestions with source provenance and any proposed due date.
3. The traveler explicitly accepts or dismisses each suggestion. AI output is never promoted automatically.
4. Accepted items appear in the visit's follow-up list and Action Center.
5. The traveler moves an item through OPEN → IN_PROGRESS → COMPLETED. OPEN and IN_PROGRESS may also be dismissed.
6. Reopening a COMPLETED or DISMISSED item requires a user-entered reason. Every decision and transition is retained in the event history.

Manual follow-ups are supported by the same API contract; the visit record form also remains a human-authored source for follow-up suggestions.

## Domain and invariants

### VisitFollowUp

`visit_follow_ups` is scoped by organization, Trip and Visit. It stores:

- Source: RECORD, MINUTES_DRAFT, or MANUAL.
- Source pointer and source index where applicable, plus a source title/due-date snapshot.
- Current title, due date and status.
- Creator, confirmer and completer identity/timestamps.
- Creation and update timestamps.

A source key prevents the same suggestion from being accepted or dismissed repeatedly. A composite foreign key binds the follow-up to the same organization, Trip and Visit. Source records must be applied records on that exact Visit. Minutes must be a successful MEETING_MINUTES_DRAFT AgentRun whose input.visit_id matches the requested Visit.

### VisitFollowUpEvent

`visit_follow_up_events` stores ACCEPTED, DISMISSED, CREATED_MANUAL, STARTED, COMPLETED, REOPENED and UPDATED events with from/to status, actor, note and timestamp. The application only appends events; it does not rewrite prior events.

### Safety boundaries

- Both tables enforce organization RLS.
- Cross-tenant source pointers are rejected by service checks and composite foreign keys.
- Write endpoints use idempotency keys.
- Status changes lock the target row to serialize concurrent updates.
- Reopening a terminal item requires a non-empty, user-provided reason.
- Follow-up operations do not change Trip planning versions or Visit status.
- No Trip/Visit creation, scheduling, route changes, CRM writeback, customer message or external notification is triggered.

## APIs

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/visits/{visit_id}/follow-ups` | Current items plus unprocessed source suggestions |
| POST | `/v1/visits/{visit_id}/follow-ups` | ACCEPT / DISMISS a source suggestion or CREATE a manual item |
| PATCH | `/v1/visit-follow-ups/{follow_up_id}` | Update title/due date or transition status |

Viewer role can read. Traveler role can create, decide and update. All write requests accept an idempotency key.

## Action Center

OPEN and IN_PROGRESS follow-ups are shown in PREPARATION. COMPLETED follow-ups remain visible in COMPLETED for the existing seven-day completion window. DISMISSED items remain in the visit history but do not create ongoing Action Center work.

The action points back to the Trip; the visit workspace remains the editing surface. There is no second task-detail subsystem.

## iOS

- Domain models decode the API feed, source provenance and event history.
- Remote repository exposes GET / POST / PATCH with idempotency keys.
- Local repository persists suggestions, decisions and event history in the existing local database.
- The Visit Record sheet shows AI/record suggestions, explicit accept/dismiss controls, current items, status actions and expandable audit history.
- Reopening prompts for a reason.
- The local Action Center aggregates active and recently completed follow-ups.

## Verification

- Backend: Ruff, mypy, OpenAPI contract check, docs/migration check, Alembic migration and metadata drift.
- Integration coverage: human decision gate, idempotent replay, dismiss-and-hide, exact VisitRecord/AgentRun provenance, cross-tenant isolation, state transitions, reopen reason, audit history, manual creation/edit, Action Center aggregation, and no Trip version/Visit status mutation.
- iOS: SwiftLint, Xcode Build, feed/model decoding, endpoint path/body and idempotency tests.

## Deferred

Automatic CRM writeback, outbound customer messaging, automatic scheduling, route optimization, automatic task assignment, cross-visit commitment inference and notification campaigns are deliberately deferred. They require separate authorization, workflow ownership and conflict-resolution rules.
