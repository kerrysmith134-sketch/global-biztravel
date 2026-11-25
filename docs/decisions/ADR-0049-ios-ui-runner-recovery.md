# ADR-0049: Retry XCTest runner bootstrap failures, not product failures

- Status: Accepted
- Date: 2026-11-25
- Scope: iOS UI acceptance test guard and deterministic Mission tab selection

## Context

The iOS UI acceptance job can fail before XCTest begins executing a test when the simulator runner exits during bootstrap. The result bundle reported:

- `Early unexpected exit, operation never finished bootstrapping`
- `Test crashed with signal kill before starting test execution`

The guard treated this as an ordinary assertion failure, so it did not retry. Its diagnostic grep also matched arbitrary Swift source lines containing the parameter label `error:`, which obscured the real failure. Separately, `app.buttons["任务"]` matched both the TabView item and an in-content control in the local-mode test.

## Decision

1. Classify only the known pre-test XCTest runner bootstrap signatures as transient infrastructure failures.
2. On that signature, clean stale Xcode build state, restart the explicitly selected simulator by UDID, and retry.
3. Keep ordinary XCTest assertion failures and compiler diagnostics non-retriable.
4. Increase the real-service UI acceptance attempt budget to two; a true product/test failure still stops after its first attempt.
5. Filter diagnostics to actual compiler location errors, XCTest failed cases, and runner bootstrap failures.
6. Query primary navigation tabs through `app.tabBars.buttons`, not the unscoped `app.buttons` collection.

## Consequences

- A transient runner crash before tests start no longer masquerades as a product failure.
- Real assertion failures are still surfaced immediately, without blind retries.
- Local-mode navigation tests select the actual Mission tab deterministically.
