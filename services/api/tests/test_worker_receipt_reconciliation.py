from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from app.workers import main as worker


def test_receipt_reconciliation_failure_uses_short_retry_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    organization_id = "11111111-1111-1111-1111-111111111111"
    previous_value = worker._last_receipt_reconciliation.pop(organization_id, None)
    clock = {"now": 1_000.0}
    settings = SimpleNamespace(
        receipt_orphan_reconcile_interval_seconds=86_400,
        receipt_orphan_grace_seconds=86_400,
    )
    attempts = {"count": 0}
    expected_report = SimpleNamespace(failed_deletions=1)

    monkeypatch.setattr(worker, "get_settings", lambda: settings)
    monkeypatch.setattr(worker, "time", SimpleNamespace(monotonic=lambda: clock["now"]))

    @contextmanager
    def fake_org_session(_organization_id: str):
        yield object()

    monkeypatch.setattr(worker, "org_session", fake_org_session)

    def fail_once(_session, _organization_id, *, grace_seconds: int):
        attempts["count"] += 1
        assert grace_seconds == settings.receipt_orphan_grace_seconds
        if attempts["count"] == 1:
            raise RuntimeError("temporary object-store outage")
        return expected_report

    monkeypatch.setattr(
        worker.receipt_storage_reconciliation_service,
        "reconcile_orphaned_receipt_objects",
        fail_once,
    )

    try:
        with pytest.raises(RuntimeError, match="temporary object-store outage"):
            worker.reconcile_receipt_orphans(organization_id)

        # A transient failure must not defer the next try for the full daily interval.
        assert worker.reconcile_receipt_orphans(organization_id) is None
        assert attempts["count"] == 1

        clock["now"] += 300
        assert worker.reconcile_receipt_orphans(organization_id) is expected_report
        assert attempts["count"] == 2

        # A completed scan with failed object deletions is also a partial failure.
        # It receives the same bounded cooldown rather than the full daily interval.
        clock["now"] += 299
        assert worker.reconcile_receipt_orphans(organization_id) is None
        assert attempts["count"] == 2
        clock["now"] += 1
        assert worker.reconcile_receipt_orphans(organization_id) is expected_report
        assert attempts["count"] == 3
    finally:
        worker._last_receipt_reconciliation.pop(organization_id, None)
        if previous_value is not None:
            worker._last_receipt_reconciliation[organization_id] = previous_value
