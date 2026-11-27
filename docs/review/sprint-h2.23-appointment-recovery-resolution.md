# H2.23 — Appointment Recovery Decision & Resolution Lifecycle

## Goal

Promote visit-scoped Appointment Recovery actions into the existing human-confirmed VisitFollowUp lifecycle. No parallel task manager or approval table.

## Data flow

1. A successful APPOINTMENT_RECOVERY AgentRun stores the action list, including Visit ID, scope, action code, rationale and required inputs.
2. The visit follow-up feed discovers matching actions for the exact Visit and returns them as pending suggestions.
3. The traveler ACCEPTs or DISMISSEs one action through the existing idempotent follow-up endpoint.
4. ACCEPT creates a VisitFollowUp with source_type=APPOINTMENT_RECOVERY, source_agent_run_id and source_index; the existing Action Center picks it up.
5. The traveler tracks the accepted item through the existing lifecycle. Completion means the human work item is complete, not that the appointment was changed.

## Integrity and concurrency

- AgentRun must be APPOINTMENT_RECOVERY / SUCCEEDED, same organization and Trip.
- Its WorkflowInstance must be the completed APPOINTMENT_RECOVERY workflow for the same Trip.
- AgentRun input suggestion_id must resolve to the same Trip's ProactiveSuggestion.
- Workflow payload and suggestion proposed_action must both point to the exact AgentRun and Workflow IDs.
- Action index must resolve to a supported action and exact Visit ID. Trip-level actions cannot become Visit tasks.
- Decision locks the AgentRun; the existing unique source key prevents duplicate processing.
- Existing RLS, same-Trip composite Visit foreign key and AgentRun composite foreign key remain in force.

## API / iOS

No new endpoint. Existing visit follow-up GET returns recovery source, action code, priority, rationale and required input gaps. Existing POST performs ACCEPT / DISMISS; existing PATCH tracks lifecycle. iOS displays source, priority, rationale and missing inputs.

## Guardrails

- No PlanVersion / Visit / Trip mutation.
- No customer contact, outbound message, calendar write, booking, rebooking or automated schedule change.
- Confirmed customer availability must later pass through the existing plan-change workflow.
- A trip-level impact-revalidation action remains at Trip scope.
