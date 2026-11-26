# H2.16 — Weather Signal to Active Plan Impact Triage

## Objective

Turn an external weather signal from a generic trip alert into a deterministic, auditable assessment of whether it overlaps planned customer visits.

## Implementation

- Add `external_impact_service.assess_weather_signal_impact`.
- Resolve the trip's current ACTIVE PlanVersion and its persisted PlanStops.
- Require exact `place_id` equality between the signal payload and Visit.place_id.
- Require an intersection between the PlanStop interval `[arrive_at, depart_at)` and the bounded signal validity interval `[max(observed_at, valid_from), valid_until)`.
- Persist the assessment with the existing proactive suggestion: plan version, matched stops, affected visit IDs, status, method, and explanation.
- Include PlanVersion and PlanStop references in the suggestion evidence chain.
- The approval executor preserves the assessment in its PREPARED result; it still does not modify Trip inputs, generate a replacement plan, or book/rebook travel.

## Assessment outcomes

- `NO_ACTIVE_PLAN`
- `PLACE_REFERENCE_MISSING` / `PLACE_REFERENCE_INVALID`
- `SIGNAL_WINDOW_UNBOUNDED`
- `NO_PLANNED_STOP_AT_SIGNAL_PLACE`
- `PLACE_MATCH_NO_TIME_OVERLAP`
- `PLACE_AND_TIME_OVERLAP`

The last status means potential exposure only. It is not a claim that the visit is impossible or must be moved.

## Safety and scope

- No LLM inference, place-name fuzzy matching, or proximity guess is used.
- A current-weather observation without a bounded validity window is not projected into future travel days.
- Flight alerts remain manual-review signals until an actual flight-booking or itinerary-leg identifier is linked to the trip.
- Currency alerts remain manual-review signals until the trip has explicit currency exposure amounts and dates.
- Existing visa, FX, flight, and approval policy workflows are not duplicated.
- No database migration or OpenAPI endpoint change is required.

## Verification

An integration test creates three planned stops: one with exact place and time overlap, one at the same place outside the signal window, and one at a different place during the signal window. Only the first stop may be classified as potentially affected. It also verifies that approving the suggestion does not mutate Trip version or active PlanVersion.

The test is added to the PostgreSQL integration suite. It must pass under the repository's strict GitHub Actions run before this slice is considered verified or deployable.


## Dedupe follows the active plan revision

For weather signals, the suggestion dedupe fingerprint includes the signal payload plus the active plan revision and affected visit IDs. If the same external observation remains valid after a new plan is activated, the runtime computes a new impact snapshot rather than silently reusing one tied to a superseded plan. The suggestion cooldown still controls user interruption; the refreshed assessment remains auditable as a suppressed suggestion when appropriate.
