# Sprint H2.11 — Opportunity to Trip Execution Handoff

## Goal

Close the first operational gap between evidence-backed market research and a real business trip. A qualified opportunity must be traceably connected to the actual Trip visit that will advance it, without creating a second task system or silently mutating travel plans.

This sprint reuses the existing Market Workspace, Market Opportunity, Workspace–Trip link, Trip and Visit domains.

## User flow

1. A user reviews evidence and manually qualifies an opportunity.
2. The user explicitly transitions it to `ACTION_PLANNED`.
3. The user associates an existing Trip with the research Workspace, using the existing Workspace–Trip action.
4. The execution panel lists eligible planned/scheduled visits from active Trips in that Workspace.
5. The user selects the relevant visit and may add a relationship note.
6. The system stores a reversible, auditable Opportunity–Visit association.
7. The user can unlink or relink the association later. This never deletes the Trip, Visit or visit record.

## Domain contract

### New relation

`market_opportunity_visit_links` is a separate association table, not a new task entity and not a column on Visit. One visit may support more than one opportunity, and one opportunity may require visits across multiple Trips.

The relation stores:

- organization, Workspace, Opportunity, Trip and Visit IDs;
- `LINKED` / `UNLINKED` state;
- link note, actor and timestamp;
- unlink note, actor and timestamp.

The unique identity is organization + Workspace + Opportunity + Trip + Visit. Relinking reuses the original row, preserving history.

### Required invariants

- Linking is allowed only when the Opportunity is in `ACTION_PLANNED`. The API does not qualify an opportunity or advance its stage.
- The Trip must have an active Workspace–Trip association.
- The selected Visit must belong to that exact Trip and organization, and must be `PLANNED` or `SCHEDULED`.
- Database composite foreign keys bind the Visit to the same organization and Trip, the Opportunity to the same Workspace, and the Trip to the Workspace–Trip association.
- A Workspace–Trip association cannot be unlinked while it still has active Opportunity–Visit links. Users must unlink the child associations first.
- Linking/unlinking does not touch the Trip planning version and does not change Visit status.
- No action is taken against a customer record, CRM, calendar, map route or external system.

## API

- `GET /v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/execution`
  - Returns current opportunity stage, a blocking reason, linked/history rows and eligible visits from active Workspace Trips.
- `POST /v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/visits`
  - Creates or restores an idempotent relation to an existing Trip Visit.
- `POST /v1/market/workspaces/{workspace_id}/opportunities/{opportunity_id}/visits/{visit_id}:unlink`
  - Soft-unlinks the relation and records the actor and reason.
- Existing Workspace–Trip unlink now refuses to orphan active child associations.

All writes use the existing idempotency guard and Traveler role. Reads require Viewer role.

## iOS

The Opportunity impact review sheet now has an execution section:

- displays stage and any reason execution is blocked;
- shows linked and historical visits, including Trip, date range, customer and place;
- offers visits already present in active Workspace Trips;
- captures an optional relationship note;
- requires confirmation before unlinking;
- refreshes the execution context after a successful mutation.

The interface does not create a Trip or Visit automatically. Users continue to use the existing trip and visit creation workflows, then connect the right records here.

## Migration

Revision `0043_market_opp_visit_link` follows `0042_market_claim_lineage`. It adds the composite Visit identity constraint required by the database FK and creates the relation table and indexes.

## Verification

Integration coverage verifies:

- pre-qualification / pre-`ACTION_PLANNED` link rejection;
- candidate selection only from linked Workspace Trips;
- idempotency-key replay and semantic duplicate reuse;
- link, unlink, history and relink behavior;
- parent Trip unlink protection while active child links exist;
- Trip version remains unchanged by association writes;
- actor and activity-event audit persistence.

iOS tests verify endpoint paths, idempotency keys, request encoding, and decoding of linked/history and candidate payloads.

## Explicitly deferred

This is a reliable execution handoff, not yet a fully autonomous Trip Agent. Automatic Trip creation, AI-generated visit proposals, customer/entity auto-matching, route optimization, calendar booking, external messages, on-site record generation and CRM sync remain separate authorized workflows. They must be added with explicit approval and their own evidence, safety and idempotency contracts.
