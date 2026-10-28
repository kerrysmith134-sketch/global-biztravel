"""S3 验收：结构化日志、trace/延迟字段与按 Trip 的审计回放。"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from app.db import org_session
from app.main import app
from app.models.reliability import OutboxEvent
from app.models.trip import Trip
from app.observability.logging import JsonFormatter, route_template, trip_id_from_path
from app.services import proactive_service
from fastapi.testclient import TestClient
from sqlalchemy import delete

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "33333333-3333-3333-3333-333333333333"
USER_A = "22222222-2222-2222-2222-222222222222"
USER_B = "44444444-4444-4444-4444-444444444444"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def headers(org: str = ORG_A, user: str = USER_A) -> dict[str, str]:
    return {"X-Org-Id": org, "X-User-Id": user}


def test_request_log_is_json_ready_with_trace_and_latency(client: TestClient, caplog) -> None:
    caplog.set_level(logging.INFO, logger="app.http")
    resp = client.get("/v1/health")
    assert resp.status_code == 200

    records = [
        record
        for record in caplog.records
        if record.name == "app.http" and record.getMessage() == "http_request"
    ]
    assert records, "未产生 http_request 日志"
    record = records[-1]
    assert record.path == "/v1/health"
    assert record.status_code == 200
    assert record.latency_ms >= 0
    assert record.trace_id
    assert record.usecase == "GET /v1/health"


def test_json_formatter_only_emits_allowlisted_fields() -> None:
    record = logging.LogRecord(
        name="app.http",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="http_request",
        args=(),
        exc_info=None,
    )
    record.trace_id = "trace-1"
    record.org_id = str(uuid.uuid4())
    record.latency_ms = 12.5
    record.secret = "should-not-appear"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "http_request"
    assert payload["trace_id"] == "trace-1"
    assert payload["latency_ms"] == 12.5
    assert "secret" not in payload


def test_route_template_and_trip_id_extraction() -> None:
    trip_id = "31cc2b1c-acaf-4d81-a12f-50267e9e3d6e"
    path = f"/v1/trips/{trip_id}/plans/abc"
    assert route_template(path) == "/v1/trips/{id}/plans/abc"
    assert trip_id_from_path(path) == trip_id
    assert trip_id_from_path("/v1/health") is None


def test_trip_audit_returns_lifecycle_timeline(client: TestClient) -> None:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(),
        json={
            "name": f"S3 审计·{uuid.uuid4().hex[:8]}",
            "window_start": (now + timedelta(hours=48)).isoformat(),
            "window_end": (now + timedelta(days=4)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]
    try:
        with org_session(UUID(ORG_A)) as session:
            proactive_service.scan_trip(session, UUID(ORG_A), UUID(trip_id))

        audit = client.get(f"/v1/trips/{trip_id}/audit", headers=headers())
        assert audit.status_code == 200, audit.text
        events = audit.json()
        kinds = {event["kind"] for event in events}
        assert {"suggestion", "suggestion_audit", "outbox"} <= kinds
        assert all(event["at"] for event in events)
        # 时间线按时间倒序（第一条不早于最后一条）。
        assert events[0]["at"] >= events[-1]["at"]

        stolen = client.get(
            f"/v1/trips/{trip_id}/audit", headers=headers(ORG_B, USER_B)
        )
        assert stolen.status_code == 404
    finally:
        with org_session(UUID(ORG_A)) as session:
            session.execute(
                delete(OutboxEvent).where(OutboxEvent.aggregate_id == UUID(trip_id))
            )
            session.execute(delete(Trip).where(Trip.id == UUID(trip_id)))
