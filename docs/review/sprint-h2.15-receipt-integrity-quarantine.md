# H2.15 — Receipt Integrity Quarantine & Verified Recovery

## Objective

Close the gap between object-store diagnosis and the business-facing receipt lifecycle. A ReceiptAsset must not remain AVAILABLE after its original object is proven missing or its bytes fail the immutable SHA-256 check.

## Changes

- Missing objects confirmed by exact-key metadata inspection quarantine the corresponding ReceiptAsset.
- Listing metadata is compared with the immutable byte length. Size mismatches quarantine the asset without downloading receipt bytes.
- A quarantined object may return to AVAILABLE only after a read verifies both the original byte length and the original SHA-256.
- Receipt downloads and stored-asset OCR share the same integrity-aware read path. A confirmed missing object or hash mismatch is returned as a stable 503 integrity error; the route marks the transaction for an explicit integrity-state commit, while ordinary error responses remain rollback-only.
- Temporary object-store failures are not interpreted as data loss. A failed GET is quarantined only when an independent stat confirms the object is absent.
- The hourly worker reports quarantine, verified recovery, and integrity failures separately. Existing two-window orphan cleanup and default-off deletion policy are unchanged.

## Safety invariants

1. Receipt identity, hash, report snapshots, and original metadata are never rewritten during quarantine or recovery.
2. A replacement object with the wrong size or hash remains quarantined, even if it reappears at the same key.
3. Orphan cleanup never deletes an object that has a ReceiptAsset metadata row, including quarantined assets.
4. Expense attachment continues to require AVAILABLE assets; a quarantined receipt cannot be newly attached.
5. No storage outage is silently converted into a missing-object diagnosis.

## Verification coverage added

- Reconciliation quarantines an asset after confirmed object loss.
- A same-length replacement with different bytes fails SHA-256 verification and stays quarantined.
- Exact original bytes restore the asset and resolve the missing-object incident.
- Download-time same-length corruption returns the stable integrity error and persists quarantine; restoring exact bytes permits a subsequent verified read.

## Execution note

Source and regression tests are added in this change. The full PostgreSQL-backed suite and GitHub Actions have not been run from this execution environment; deployment must remain gated on the repository's strict CI and migration/drift checks.
