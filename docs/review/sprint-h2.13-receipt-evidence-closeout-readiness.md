# H2.13 — Receipt Evidence & Trip Expense Readiness

## Goal

Make a business trip verifiably reimbursement-ready, not merely marked COMPLETED. Preserve every original receipt, link it to the corresponding expense and immutable report snapshot, and provide an evidence-based readiness assessment with explicit gaps and next actions.

This sprint follows H2.12's human-confirmed visit follow-up lifecycle. It does not introduce a second task system and does not automatically submit or approve an expense report.

## Existing capability and verified gap

The current code already has:

- Trip expenses with category, amount, currency, date, note and a text `receipt_ref`.
- Tesseract OCR that returns merchant, amount and date candidates.
- Expense-report snapshots that copy expense metadata and `receipt_ref`.
- Trip outcome and budget aggregates.
- An existing Action Center and Trip status lifecycle.

The current OCR route reads an uploaded file and returns candidate fields, but does not persist the original file. The expense-entry sheet does not expose a receipt picker/capture flow. The repository tree contains no receipt attachment or object-storage implementation. Therefore `receipt_ref` is currently only a pointer supplied by the caller, not proof that the original receipt has been preserved and can be retrieved.

## User journey

1. The traveler captures or selects a receipt while entering an expense.
2. The original image is saved first and receives a stable Receipt Asset ID.
3. OCR reads the stored original and produces candidate merchant/date/amount/currency fields with source provenance.
4. The traveler reviews and confirms the extracted fields; OCR never creates or changes a TripExpense by itself.
5. A saved expense references the Receipt Asset. Multiple expenses may link to the same asset only when explicitly confirmed (for example, a multi-item receipt).
6. The report draft snapshots the receipt reference and relevant metadata along with amount/category/date/note.
7. The Trip Closeout Readiness view evaluates visits, expenses, receipts and report state. It shows hard blockers separately from warnings and evidence gaps.
8. The traveler resolves blockers or explicitly acknowledges exceptions, reviews the report snapshot, and submits it. Approval remains an authorized human action.

## Domain design

### ReceiptAsset

Create an organization-scoped asset record with:

- Stable UUID, organization ID and Trip ID.
- Opaque storage key; never expose a raw filesystem path or bucket key.
- Original filename (sanitized), detected MIME type, byte length, SHA-256 and upload timestamp.
- Uploading user, storage status and optional OCR AgentRun reference.
- Retention/deletion state and audit timestamps.

Store binary bytes outside PostgreSQL behind a storage interface. Development/test uses a private filesystem adapter; production uses a configurable S3-compatible adapter. Do not bind the domain model to a vendor-specific SDK.

The API must authorize every upload/read against organization and Trip membership. Enforce file size and content-type allowlists using byte sniffing, generate storage keys server-side, and prevent path traversal. Never trust a client-supplied `receipt_ref` as a storage path.

### Expense and report linkage

- Add a nullable `receipt_asset_id` to TripExpense with a same-organization/Trip constraint.
- Keep legacy `receipt_ref` readable during migration; new writes use Receipt Asset IDs.
- ExpenseReportItem snapshots `receipt_asset_id`, receipt hash and original metadata at report creation. The report remains an immutable snapshot even if the expense is later edited.
- A submitted or approved report must keep access to the referenced original asset. Asset deletion is blocked while referenced by a report; retention is handled as an explicit administrative operation.
- Preserve existing multi-currency behavior. Never invent FX conversions.

### OCR provenance

OCR becomes an operation on a stored ReceiptAsset, not an ephemeral multipart upload. Save the OCR result/candidates and link the AgentRun to the asset. Preserve:

- Original image reference and content hash.
- OCR engine/version and execution timestamp.
- Extracted candidates, confidence/quality metadata when available, and raw-text retention policy.
- Human-confirmed field values and the confirming actor/time.

OCR results remain suggestions. They cannot automatically create expenses, alter amounts, submit reports, or approve reports.

## Trip closeout readiness

Expose a read model; do not add a new Trip status or mutate Trip/Visit/Expense/Report records during assessment.

Proposed endpoint:

- `GET /v1/trips/{trip_id}/closeout-readiness`

Return a versioned assessment containing:

- `status`: BLOCKED, READY_FOR_REVIEW, REPORT_SUBMITTED, REPORT_APPROVED, or READY_WITH_EXCEPTIONS.
- Hard blockers: visits still PLANNED/SCHEDULED; completed/missed visits missing an outcome record; expenses not yet included in a report snapshot; a rejected report needing correction.
- Evidence gaps/warnings: expenses without a receipt asset, unreadable receipt, OCR fields not human-confirmed, unresolved business follow-ups, and multi-currency totals requiring separate review.
- Counts and currency-grouped totals, never a misleading single converted total.
- Evidence references: Visit IDs/VisitRecord IDs, FollowUp IDs, Expense IDs, Receipt Asset IDs and Expense Report IDs.
- Next-action hints that route users to the existing Visit, Expense, Receipt or Expense Report workflows.

Business follow-ups are not automatically hard blockers for financial closeout. An unresolved customer commitment is a business warning, not a reason to falsify or delay reimbursement readiness. Receipt requirements must be policy-driven; absent an organization/jurisdiction policy, missing receipts are surfaced as exceptions instead of silently assuming a universal rule.

The assessment is derived from current facts and does not increment the Trip planning version.

## Action Center integration

Reuse the current Action Center. Surface a concise, source-linked item for each actionable blocker, such as:

- Missing VisitRecord for a closed visit.
- Expense not yet added to a report.
- Receipt missing or unreadable.
- Expense report rejected or awaiting approval.

Do not turn every warning into a notification. Deduplicate by source entity and issue type. User resolution must be verified from persisted evidence before the item disappears.

## iOS experience

- Add receipt capture/photo-library selection to ExpenseEntrySheet.
- Show original image preview, upload/local-save state, OCR candidates and human confirmation state.
- Preserve receipts in the local-first store while offline; enqueue upload as a durable sync operation when online.
- Never mark a receipt as uploaded until the server confirms its Receipt Asset ID.
- Add a Trip Closeout panel with an overall status, blocker count, warning count, currency-separated summary and source-linked evidence rows.
- Reuse existing expense-report create/submit/approval workflows. No parallel report editor.

## Safety and invariants

- Tenant-scoped access for upload, download, OCR and asset references.
- A TripExpense can only reference a ReceiptAsset from the same organization and Trip.
- A report snapshot pins the evidence identity/hash used at submission.
- OCR never writes financial values without a human confirmation action.
- No automatic expense creation, report submission, report approval, customer message or CRM writeback.
- Readiness assessment is read-only. Only explicit user actions change Trip/Expense/Report state.
- All write endpoints are idempotent and audited.
- Original files are not placed in public buckets or exposed via guessable URLs.

## Suggested implementation sequence

1. Storage abstraction, ReceiptAsset model/migration, tenant RLS and authorized upload/download.
2. Expense and ExpenseReportItem linkage with snapshot semantics and compatibility for legacy `receipt_ref`.
3. Stored-asset OCR with AgentRun provenance and explicit human-confirm endpoint.
4. iOS local receipt vault, capture/preview, upload queue and expense editor integration.
5. Closeout readiness service/schema/endpoint and source-linked Action Center items.
6. iOS Trip Closeout panel and regression tests.

## Acceptance criteria

- Uploading a receipt persists the original bytes and returns an opaque, stable asset ID.
- A different organization cannot read, OCR, attach or infer the asset.
- A receipt attached to an expense is preserved in the report snapshot with its hash and original metadata.
- OCR output is traceable to the stored original and remains unconfirmed until the user accepts it.
- Duplicate upload/retry behavior is idempotent and does not create duplicate assets.
- Offline capture does not lose the original; failed upload is visibly pending, not falsely complete.
- Readiness results cite the exact source records and distinguish blockers from warnings.
- Resolving a blocker changes the next assessment only after the underlying source fact is persisted.
- Multi-currency reports remain separated by currency.
- Existing visit, follow-up, Trip planning-version, expense and approval invariants remain intact.

## Explicitly deferred

Automatic payment, external accounting-system posting, automatic report approval, jurisdiction-specific tax-policy inference without configured rules, and autonomous customer communications are outside H2.13.
