"""M6 验收：指标树、快照幂等与全库 RLS 兜底。

用一个**全新的组织**做断言，避免共享开发库里累积的历史数据污染分母；测试结束删掉整个组织，
其下业务表由外键级联回收。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.db import org_session, owner_session
from app.main import app
from app.models.identity import Membership, Organization
from app.models.llm import LLMInvocation
from app.models.memory import TripMemorySummary
from app.models.metrics import MetricSnapshot
from app.models.planning import PlanVersion
from app.models.proactive import ProactiveSuggestion
from app.models.reliability import BackgroundJob, OutboxEvent
from app.services import metrics_service
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text

pytestmark = [pytest.mark.integration, pytest.mark.acceptance]

USER_A = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def new_org() -> str:
    with owner_session() as session:
        org = Organization(name=f"M6 指标组织 {uuid.uuid4().hex[:6]}", slug=f"m6-{uuid.uuid4().hex[:10]}")
        session.add(org)
        session.flush()
        session.add(Membership(organization_id=org.id, user_id=uuid.UUID(USER_A), role="OWNER"))
        return str(org.id)


def drop_org(org_id: str) -> None:
    with owner_session() as session:
        # organizations 的 RLS 策略 force=False，owner 可以直接删；业务表按 FK 级联。
        session.execute(delete(Organization).where(Organization.id == uuid.UUID(org_id)))


def headers(org_id: str) -> dict[str, str]:
    return {"X-Org-Id": org_id, "X-User-Id": USER_A}


def seed_data(client: TestClient, org_id: str) -> str:
    now = datetime.now(UTC)
    trip = client.post(
        "/v1/trips",
        headers=headers(org_id),
        json={
            "name": "M6 指标样本出行",
            "window_start": (now + timedelta(days=10)).isoformat(),
            "window_end": (now + timedelta(days=12)).isoformat(),
            "countries": ["DE"],
        },
    )
    assert trip.status_code == 201, trip.text
    trip_id = trip.json()["id"]

    with org_session(uuid.UUID(org_id)) as session:
        org_uuid = uuid.UUID(org_id)
        trip_uuid = uuid.UUID(trip_id)
        session.add_all(
            [
                PlanVersion(
                    organization_id=org_uuid,
                    trip_id=trip_uuid,
                    version_number=1,
                    status="ACTIVE",
                    problem_hash="sha256:a",
                    solver="heuristic",
                    trip_version=1,
                    validation={"feasible": True},
                    unplaced=[],
                ),
                PlanVersion(
                    organization_id=org_uuid,
                    trip_id=trip_uuid,
                    version_number=2,
                    status="DRAFT",
                    problem_hash="sha256:b",
                    solver="heuristic",
                    trip_version=1,
                    validation={"feasible": False},
                    unplaced=[{"visit_id": str(uuid.uuid4()), "reason": "NO_FEASIBLE_SLOT"}],
                ),
                BackgroundJob(
                    organization_id=org_uuid, job_type="plan_trip", status="SUCCEEDED"
                ),
                BackgroundJob(organization_id=org_uuid, job_type="plan_trip", status="FAILED"),
                OutboxEvent(
                    organization_id=org_uuid,
                    aggregate_type="trip",
                    aggregate_id=trip_uuid,
                    event_type="plan.version.created",
                    status="DONE",
                ),
                OutboxEvent(
                    organization_id=org_uuid,
                    aggregate_type="trip",
                    aggregate_id=trip_uuid,
                    event_type="plan.not_feasible",
                    status="DEAD",
                ),
            ]
        )
        for index, status in enumerate(("EXECUTED", "EXECUTED", "DISMISSED")):
            session.add(
                ProactiveSuggestion(
                    organization_id=org_uuid,
                    trip_id=trip_uuid,
                    suggestion_type="VISA_REQUIREMENT_REMINDER",
                    status=status,
                    title="样本建议",
                    rationale="样本",
                    evidence=[],
                    proposed_action={},
                    confidence=0.9,
                    requires_approval=True,
                    severity="HIGH",
                    dedupe_key=f"sample-{index}",
                    push_status="PUSHED",
                    notified_at=now,
                    feedback_penalty=0,
                )
            )
        for index, (degraded, cached) in enumerate(
            ((False, False), (False, False), (True, False), (False, True))
        ):
            session.add(
                LLMInvocation(
                    organization_id=org_uuid,
                    task="draft_meeting_minutes" if index % 2 == 0 else "explain_plan",
                    model="stub",
                    prompt_template_id="t",
                    prompt_version="v1",
                    input_hash=f"sha256:{index}",
                    output_json={"text": "x"},
                    degraded=degraded,
                    cached=cached,
                )
            )
        session.add(
            TripMemorySummary(
                organization_id=org_uuid,
                trip_id=trip_uuid,
                kind="TRIP_REVIEW",
                summary="样本摘要",
                decisions=[],
                follow_ups=[],
                lessons=[],
                confidence=0.8,
                source_ref=f"trip:{trip_id}:review",
            )
        )
    return trip_id


def test_metric_tree_computes_from_real_rows(client: TestClient) -> None:
    org_id = new_org()
    try:
        seed_data(client, org_id)
        with org_session(uuid.UUID(org_id)) as session:
            metrics = {item.key: item for item in metrics_service.compute_metrics(session, org_id)}

        assert set(metrics) == {
            "planning.validator_pass_rate",
            "planning.activation_rate",
            "proactive.adoption_rate",
            "proactive.false_positive_rate",
            "reliability.job_success_rate",
            "reliability.dlq_rate",
            "ai.degraded_rate",
            "ai.cache_hit_rate",
            "memory.summaries_total",
        }
        assert metrics["planning.validator_pass_rate"].value == 0.5
        assert metrics["planning.validator_pass_rate"].sample_size == 2
        assert metrics["planning.activation_rate"].value == 1.0
        assert metrics["planning.activation_rate"].sample_size == 1
        assert metrics["proactive.adoption_rate"].value == round(2 / 3, 4)
        assert metrics["proactive.false_positive_rate"].value == round(1 / 3, 4)
        assert metrics["reliability.job_success_rate"].value == 0.5
        assert metrics["reliability.dlq_rate"].value == 0.5
        assert metrics["ai.degraded_rate"].value == 0.25
        assert metrics["ai.cache_hit_rate"].value == 0.25
        assert metrics["memory.summaries_total"].value == 1
        assert metrics["planning.validator_pass_rate"].target == ">= 0.90"

        summary = client.get("/v1/metrics/summary", headers=headers(org_id))
        assert summary.status_code == 200, summary.text
        body = {item["key"]: item for item in summary.json()["metrics"]}
        assert body["reliability.job_success_rate"]["sample_size"] == 2
    finally:
        drop_org(org_id)


def test_snapshot_is_idempotent_per_window(client: TestClient) -> None:
    org_id = new_org()
    try:
        seed_data(client, org_id)
        first = client.post("/v1/metrics:snapshot", headers=headers(org_id))
        assert first.status_code == 200, first.text
        assert len(first.json()) == 9

        second = client.post("/v1/metrics:snapshot", headers=headers(org_id))
        assert second.status_code == 200, second.text
        assert len(second.json()) == 9

        with org_session(uuid.UUID(org_id)) as session:
            count = session.scalar(
                select(func.count()).select_from(MetricSnapshot).where(
                    MetricSnapshot.organization_id == uuid.UUID(org_id)
                )
            )
        assert count == 9  # 同窗口覆盖，不堆重复行

        history = client.get(
            "/v1/metrics/snapshots",
            headers=headers(org_id),
            params={"key": "reliability.dlq_rate"},
        )
        assert history.status_code == 200, history.text
        assert len(history.json()) == 1
        assert history.json()[0]["value"] == 0.5
    finally:
        drop_org(org_id)


def test_all_org_tables_force_rls() -> None:
    org_id = new_org()
    try:
        with org_session(uuid.UUID(org_id)) as session:
            rows = session.execute(
                text(
                    """
                    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public'
                      AND c.relkind = 'r'
                      AND EXISTS (
                        SELECT 1 FROM information_schema.columns col
                        WHERE col.table_schema = 'public'
                          AND col.table_name = c.relname
                          AND col.column_name = 'organization_id'
                      )
                    ORDER BY c.relname
                    """
                )
            ).all()
        assert rows
        for name, enabled, forced in rows:
            if name in ("memberships", "organization_invitations"):
                # 身份开通表：owner 需要绕过（ADR-0002 的 bootstrap 例外）。
                continue
            assert enabled is True, f"{name} 未启用 RLS"
            assert forced is True, f"{name} 未强制 RLS"
    finally:
        drop_org(org_id)
