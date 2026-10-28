"""可靠性地基的**真实数据库**集成测试（设计文档 v2.0 第 6、7 章）。

这里验证的是"只有真库 + 真事务 + 真并发才能暴露"的那一类行为，而不是被 mock 抹平的接口形状：

- **任务队列**：``FOR UPDATE SKIP LOCKED`` 抢占在 6 个并发线程下真的把 4 条任务各发给唯一一个 Worker；
- **幂等入队**：``dedupe_key`` 复用同一在途任务，任务进入终态后才允许同 key 重新入队；
- **租约与心跳**：心跳只在持有者名下成功；租约过期后任务被回收重排，``attempts`` 不被回退；
- **退避重试**：可重试失败推后 ``available_at``、次数用尽转 ``DEAD``；不可重试失败一次到位 ``FAILED``；
- **Outbox 两级去重**：事件级按 ``dedupe_key`` 复用，交付级按 ``(event_id, consumer)`` 唯一；
- **幂等消费与死信**：同一交付只被消费一次；处理器持续失败或事件类型无人认领会转入死信；
- **消费者副作用**：``trip_status_sync`` 真的把出行从 ``PLANNING`` 推到 ``READY``、或在不可行时退回 ``DRAFT``；
- **端到端**：``plans:generate``（202 + job_id，重放字节一致）→ Worker 执行 → 计划落库 → 订阅者改出行状态。

运行前置：容器 ``zhi-tu-pg`` 已就绪（端口 55432）、迁移已到 head、``scripts/seed_dev.py`` 已跑过。

    .venv/bin/python -m pytest services/api/tests/test_reliability_integration.py -v -m integration

**测试卫生**：打的是共享开发库，所以每个用例都用 ``tracker`` 记录本轮写下的 task / 事件 / 出行 / 地点 /
幂等键，teardown 时按依赖顺序清理干净。凡是可能出现跨次运行干扰的地方都用一次性标识隔离：
任务的 ``job_type`` 每次随机（抢占时按它过滤，不会抢到别人的任务）、消费者名随机、``Idempotency-Key`` 随机
（``IdempotencyRecord.begin`` 不检查 ``expires_at``，写死键会与上一轮的旧记录冲突）。
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest
from app.api.routes.plans import (
    PLANS_ACTIVATE_ENDPOINT,
    PLANS_GENERATE_ENDPOINT,
)
from app.db import org_session
from app.main import app
from app.models.geo import Place
from app.models.planning import (
    LIFECYCLE_FIELDS,
    PLAN_VERSION_CONTENT_FIELDS,
    PlanStop,
    PlanVersion,
    PlanVersionImmutable,
)
from app.models.reliability import BackgroundJob, IdempotencyRecord, OutboxDelivery, OutboxEvent
from app.models.trip import Trip
from app.services import job_service, outbox_service, plan_change_service
from app.workers import main as workers_main
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration

ORG_A = "11111111-1111-1111-1111-111111111111"  # 安纳瑞能源
USER_A = "22222222-2222-2222-2222-222222222222"  # kerry@anari.example（OWNER）
HOME_TZ = "Asia/Shanghai"
SHANGHAI_TZ = timezone(timedelta(hours=8))


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def tracker() -> Iterator[dict[str, list]]:
    """记录本轮写下的东西，退出时按依赖顺序清理，让开发库回到基线。"""
    state: dict[str, list] = {
        "jobs": [],
        "events": [],
        "trips": [],
        "places": [],
        "idempotency": [],
    }
    yield state
    with org_session(UUID(ORG_A)) as session:
        if state["events"]:
            session.execute(
                delete(OutboxDelivery).where(OutboxDelivery.event_id.in_(state["events"]))
            )
            session.execute(delete(OutboxEvent).where(OutboxEvent.id.in_(state["events"])))
        if state["jobs"]:
            session.execute(delete(BackgroundJob).where(BackgroundJob.id.in_(state["jobs"])))
        for endpoint, key in state["idempotency"]:
            session.execute(
                delete(IdempotencyRecord).where(
                    IdempotencyRecord.endpoint == endpoint, IdempotencyRecord.key == key
                )
            )
        if state["trips"]:
            # 先删计划版本（级联 plan_stops），再删出行（级联 visits），最后删地点。
            session.execute(delete(PlanVersion).where(PlanVersion.trip_id.in_(state["trips"])))
            session.execute(delete(Trip).where(Trip.id.in_(state["trips"])))
        if state["places"]:
            session.execute(delete(Place).where(Place.id.in_(state["places"])))


# ---------------------------------------------------------------------------
# 助手
# ---------------------------------------------------------------------------
def _headers(org: str = ORG_A, user: str = USER_A) -> dict[str, str]:
    return {"X-Org-Id": org, "X-User-Id": user}


def _unique_job_type() -> str:
    """随机任务类型：让抢占只可能命中本用例自己的任务。"""
    return f"it.reliability.{uuid.uuid4().hex[:12]}"


def _unique_consumer(prefix: str = "it-consumer") -> str:
    return f"{prefix}.{uuid.uuid4().hex[:12]}"


def _force_lease_expiry(job_id: UUID) -> None:
    with org_session(ORG_A) as session:
        session.execute(
            text(
                "UPDATE background_jobs "
                "   SET lease_expires_at = clock_timestamp() - interval '5 seconds' "
                " WHERE id = CAST(:job_id AS uuid)"
            ),
            {"job_id": str(job_id)},
        )


def _make_available_now(job_id: UUID) -> None:
    with org_session(ORG_A) as session:
        session.execute(
            text("UPDATE background_jobs SET available_at = clock_timestamp() WHERE id = CAST(:job_id AS uuid)"),
            {"job_id": str(job_id)},
        )


def _make_event_ready_now(event_id: UUID) -> None:
    with org_session(ORG_A) as session:
        session.execute(
            text(
                "UPDATE outbox_events SET next_attempt_at = clock_timestamp() "
                " WHERE id = CAST(:event_id AS uuid)"
            ),
            {"event_id": str(event_id)},
        )


def _job(job_id: UUID) -> BackgroundJob:
    with org_session(ORG_A) as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None, f"任务 {job_id} 不存在"
        return job


def _event_status(event_id: UUID) -> str:
    with org_session(ORG_A) as session:
        event = session.get(OutboxEvent, event_id)
        assert event is not None
        return event.status


def _delivery_status(event_id: UUID, consumer: str) -> str:
    with org_session(ORG_A) as session:
        delivery = session.scalars(
            select(OutboxDelivery).where(
                OutboxDelivery.event_id == event_id, OutboxDelivery.consumer == consumer
            )
        ).one()
        return delivery.status


def _trip_status(trip_id: UUID) -> str:
    with org_session(ORG_A) as session:
        trip = session.get(Trip, trip_id)
        assert trip is not None
        return trip.status


def _set_trip_status(trip_id: UUID, status: str) -> None:
    with org_session(ORG_A) as session:
        session.execute(
            text("UPDATE trips SET status = :status WHERE id = CAST(:trip_id AS uuid)"),
            {"status": status, "trip_id": str(trip_id)},
        )


def _claim_by_id(job_id: UUID, worker_id: str, *, lease_seconds: int = 60) -> None:
    """按 id 精确抢占一条任务（与 ``job_service.claim_next`` 语义一致）。

    这里刻意不用 ``claim_next`` 的"取最早一条"语义：集成测试打的是共享开发库，前一次运行
    若留下了 ``PENDING`` 的同类型任务，按最早一条抢就会把别人的任务抢走。声明为测试侧助手，
    真正的并发抢占由 ``claim_next`` 的用例单独覆盖。
    """
    with org_session(ORG_A) as session:
        job = session.scalars(
            select(BackgroundJob).where(BackgroundJob.id == job_id).with_for_update()
        ).one()
        assert job.status == "PENDING", f"任务 {job_id} 当前是 {job.status}，不该被抢占"
        now = session.scalar(select(func.clock_timestamp()))
        job.status = "RUNNING"
        job.attempts += 1
        job.locked_at = now
        job.locked_by = worker_id
        job.heartbeat_at = now
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        if job.started_at is None:
            job.started_at = now
        job.last_error = None
        session.flush()


def _unique_place(client: TestClient, tracker: dict[str, list], *, city: str, country: str,
                  lat: float, lon: float, tz: str) -> str:
    """建一个**名字唯一**的地点。

    ``POST /v1/places`` 是"同名 + 同城 + 同国即复用"，所以名字必须唯一，否则会复用种子地点，
    收尾时就会把别人在用的地点删掉（``visits.place_id`` 是 RESTRICT）。
    """
    resp = client.post(
        "/v1/places",
        headers=_headers(),
        json={
            "name": f"集成测试地点·{uuid.uuid4().hex[:8]}",
            "city": city,
            "country_code": country,
            "timezone": tz,
            "lat": lat,
            "lon": lon,
        },
    )
    assert resp.status_code == 201, resp.text
    place_id = resp.json()["id"]
    tracker["places"].append(UUID(place_id))
    return place_id


def _create_trip(client: TestClient, tracker: dict[str, list], *, origin_place_id: str, name: str) -> str:
    resp = client.post(
        "/v1/trips",
        headers=_headers(),
        json={
            "name": name,
            "origin_place_id": origin_place_id,
            "home_timezone": HOME_TZ,
            "currency": "CNY",
            "window_start": datetime(2026, 11, 9, 9, 0, tzinfo=SHANGHAI_TZ).isoformat(),
            "window_end": datetime(2026, 11, 15, 18, 0, tzinfo=SHANGHAI_TZ).isoformat(),
            "countries": ["DE"],
        },
    )
    assert resp.status_code == 201, resp.text
    trip_id = resp.json()["id"]
    tracker["trips"].append(UUID(trip_id))
    return trip_id


def _add_visit(client: TestClient, trip_id: str, *, place_id: str) -> str:
    resp = client.post(
        f"/v1/trips/{trip_id}/visits",
        headers=_headers(),
        json={
            "place_id": place_id,
            "title": "柏林客户拜访",
            "service_minutes": 90,
            "business_value": 500,
            "movement_mode": "AIR",
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _start_generation(client: TestClient, tracker: dict[str, list], trip_id: str) -> str:
    """调用 ``plans:generate``，返回 job_id；记下幂等键供收尾清理。"""
    key = f"it-plan-generate.{uuid.uuid4().hex}"
    tracker["idempotency"].append((PLANS_GENERATE_ENDPOINT, key))
    resp = client.post(
        f"/v1/trips/{trip_id}/plans:generate",
        headers={**_headers(), "Idempotency-Key": key},
    )
    assert resp.status_code == 202, resp.text
    job_id = resp.json()["job_id"]
    tracker["jobs"].append(UUID(job_id))
    return job_id


# ------------------------------------------------------------ 任务队列：抢占
def test_concurrent_claim_gives_each_job_to_exactly_one_worker(tracker: dict[str, list]) -> None:
    job_type = _unique_job_type()
    with org_session(ORG_A) as session:
        ids = [job_service.enqueue(session, ORG_A, job_type=job_type, payload={"n": i}).id for i in range(4)]
    tracker["jobs"].extend(ids)

    workers = 6
    barrier = threading.Barrier(workers)
    claimed: list[str | None] = [None] * workers
    errors: list[BaseException] = []

    def run(idx: int) -> None:
        try:
            barrier.wait(timeout=30)
            with org_session(ORG_A) as session:
                job = job_service.claim_next(session, worker_id=f"it-w{idx}", job_type=job_type)
                claimed[idx] = None if job is None else str(job.id)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert not errors, errors
    assert all(not thread.is_alive() for thread in threads)

    got = [item for item in claimed if item is not None]
    assert len(got) == 4, f"应有 4 次成功抢占，实际 {claimed}"
    assert sorted(got) == sorted(str(job_id) for job_id in ids)
    assert len(set(got)) == 4, "同一条任务被两个 Worker 抢到了"

    with org_session(ORG_A) as session:
        rows = session.scalars(select(BackgroundJob).where(BackgroundJob.id.in_(ids))).all()
        assert {row.status for row in rows} == {"RUNNING"}
        assert all(row.attempts == 1 for row in rows)


def test_enqueue_dedupe_key_reuses_inflight_then_allows_new_after_terminal(
    tracker: dict[str, list],
) -> None:
    job_type = _unique_job_type()
    dedupe_key = f"it-dedupe.{uuid.uuid4().hex}"

    with org_session(ORG_A) as session:
        first = job_service.enqueue(session, ORG_A, job_type=job_type, dedupe_key=dedupe_key)
    tracker["jobs"].append(first.id)

    with org_session(ORG_A) as session:
        second = job_service.enqueue(session, ORG_A, job_type=job_type, dedupe_key=dedupe_key)
        assert second.id == first.id, "在途任务存在时，同 key 入队必须复用它"
        inflight = job_service.find_inflight(session, ORG_A, dedupe_key)
        assert inflight is not None and inflight.id == first.id

    with org_session(ORG_A) as session:
        job = job_service.claim_next(session, worker_id="it-w", job_type=job_type)
        assert job is not None and job.id == first.id
        job_service.fail(session, first.id, error="终态，允许同 key 重新入队", retryable=False)

    with org_session(ORG_A) as session:
        third = job_service.enqueue(session, ORG_A, job_type=job_type, dedupe_key=dedupe_key)
        assert third.id != first.id, "任务已终态，同 key 应能新建"
    tracker["jobs"].append(third.id)


# ------------------------------------------------------- 任务队列：租约与心跳
def test_lease_recovery_requeues_job_and_heartbeat_loses_ownership(tracker: dict[str, list]) -> None:
    job_type = _unique_job_type()
    with org_session(ORG_A) as session:
        job_id = job_service.enqueue(session, ORG_A, job_type=job_type).id
    tracker["jobs"].append(job_id)

    with org_session(ORG_A) as session:
        job = job_service.claim_next(session, worker_id="it-w1", job_type=job_type, lease_seconds=60)
        assert job is not None and job.id == job_id and job.attempts == 1

    with org_session(ORG_A) as session:
        assert job_service.heartbeat(session, job_id, worker_id="it-w1") is True
        # 名字不对：不该续到别人的租约
        assert job_service.heartbeat(session, job_id, worker_id="it-intruder") is False

    _force_lease_expiry(job_id)

    with org_session(ORG_A) as session:
        touched = job_service.recover_expired_leases(session)
    assert job_id in touched

    with org_session(ORG_A) as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None
        assert job.status == "PENDING"
        assert job.attempts == 1, "回收重排不该回退 attempts（否则死循环永远不会终止）"
        assert job.locked_by is None and job.locked_at is None and job.lease_expires_at is None

    with org_session(ORG_A) as session:
        assert job_service.heartbeat(session, job_id, worker_id="it-w1") is False

    with org_session(ORG_A) as session:
        job = job_service.claim_next(session, worker_id="it-w2", job_type=job_type)
        assert job is not None and job.id == job_id and job.attempts == 2


# ----------------------------------------------------------- 任务队列：重试
def test_retryable_failure_backs_off_then_dead_and_non_retryable_fails_immediately(
    tracker: dict[str, list],
) -> None:
    job_type = _unique_job_type()
    with org_session(ORG_A) as session:
        job_id = job_service.enqueue(session, ORG_A, job_type=job_type, max_attempts=2).id
    tracker["jobs"].append(job_id)

    with org_session(ORG_A) as session:
        job = job_service.claim_next(session, worker_id="it-w1", job_type=job_type)
        assert job is not None and job.attempts == 1
        job_service.fail(session, job_id, error="第一次失败", retryable=True)

    with org_session(ORG_A) as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None
        assert job.status == "PENDING" and job.finished_at is None
        assert job.locked_by is None and job.heartbeat_at is None
        now = session.scalar(select(func.clock_timestamp()))
        assert job.available_at > now, "退避必须把 available_at 推后"

    with org_session(ORG_A) as session:
        assert job_service.claim_next(session, worker_id="it-w1b", job_type=job_type) is None

    _make_available_now(job_id)

    with org_session(ORG_A) as session:
        job = job_service.claim_next(session, worker_id="it-w2", job_type=job_type)
        assert job is not None and job.attempts == 2
        job_service.fail(session, job_id, error="次数用尽", retryable=True)

    with org_session(ORG_A) as session:
        job = session.get(BackgroundJob, job_id)
        assert job is not None
        assert job.status == "DEAD" and job.finished_at is not None
        assert job.last_error == "次数用尽"

    # 不可重试：一次到位 FAILED，不占重试预算
    other_type = _unique_job_type()
    with org_session(ORG_A) as session:
        other_id = job_service.enqueue(session, ORG_A, job_type=other_type).id
    tracker["jobs"].append(other_id)
    with org_session(ORG_A) as session:
        job_service.claim_next(session, worker_id="it-w3", job_type=other_type)
        job_service.fail(session, other_id, error="输入非法", retryable=False)
    with org_session(ORG_A) as session:
        job = session.get(BackgroundJob, other_id)
        assert job is not None
        assert job.status == "FAILED" and job.finished_at is not None


# ------------------------------------------------------------ Outbox：两级去重
def test_outbox_dedupes_event_by_key_and_delivery_by_consumer(tracker: dict[str, list]) -> None:
    aggregate_id = uuid.uuid4()
    dedupe_key = f"it-outbox.{uuid.uuid4().hex}"

    with org_session(ORG_A) as session:
        event = outbox_service.emit(
            session,
            ORG_A,
            aggregate_type=outbox_service.AGGREGATE_TRIP,
            aggregate_id=aggregate_id,
            event_type="it.event.created",
            payload={"k": "v"},
            consumers=("it-c1", "it-c2"),
            dedupe_key=dedupe_key,
        )
    tracker["events"].append(event.id)
    event_id = event.id

    with org_session(ORG_A) as session:
        again = outbox_service.emit(
            session,
            ORG_A,
            aggregate_type=outbox_service.AGGREGATE_TRIP,
            aggregate_id=aggregate_id,
            event_type="it.event.created",
            payload={"k": "v", "attempt": 2},
            consumers=("it-c1",),
            dedupe_key=dedupe_key,
        )
        assert again.id == event_id, "同 dedupe_key 应复用事件，而不是新建"
        assert again.payload.get(outbox_service.DEDUPE_FIELD) == dedupe_key

    with org_session(ORG_A) as session:
        total_events = session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.aggregate_id == aggregate_id)
        )
        assert total_events == 1
        deliveries = session.scalars(
            select(OutboxDelivery).where(OutboxDelivery.event_id == event_id)
        ).all()
        assert sorted(item.consumer for item in deliveries) == ["it-c1", "it-c2"]

    # 不同 dedupe_key ⇒ 新事件
    with org_session(ORG_A) as session:
        other = outbox_service.emit(
            session,
            ORG_A,
            aggregate_type=outbox_service.AGGREGATE_TRIP,
            aggregate_id=aggregate_id,
            event_type="it.event.created",
            payload={},
            consumers=("it-c1",),
            dedupe_key=f"it-outbox.{uuid.uuid4().hex}",
        )
        assert other.id != event_id
    tracker["events"].append(other.id)


# ------------------------------------------------------- Outbox：幂等消费
def test_publish_consumes_each_delivery_exactly_once(tracker: dict[str, list]) -> None:
    consumer = _unique_consumer()
    calls: list[UUID] = []

    def handler(session: Session, event: OutboxEvent) -> None:  # noqa: ARG001
        calls.append(event.id)

    with org_session(ORG_A) as session:
        event = outbox_service.emit(
            session,
            ORG_A,
            aggregate_type=outbox_service.AGGREGATE_TRIP,
            aggregate_id=uuid.uuid4(),
            event_type="it.counted",
            consumers=(consumer,),
        )
    tracker["events"].append(event.id)
    event_id = event.id

    with org_session(ORG_A) as session:
        report = outbox_service.publish(session, consumer=consumer, handlers={"it.counted": handler})
    assert report.claimed == 1 and report.done == 1 and report.failed == 0
    assert calls == [event_id]
    assert _delivery_status(event_id, consumer) == "DONE"
    assert _event_status(event_id) == "DONE"

    with org_session(ORG_A) as session:
        second = outbox_service.publish(session, consumer=consumer, handlers={"it.counted": handler})
    assert second.claimed == 0, "已投递的交付不该被再次认领"
    assert calls == [event_id], "处理器只应被调用一次"


# --------------------------------------------------------- Outbox：死信
def test_publish_dead_letters_event_after_retries_exhausted(tracker: dict[str, list]) -> None:
    consumer = _unique_consumer()

    def boom(session: Session, event: OutboxEvent) -> None:  # noqa: ARG001
        raise RuntimeError("handler 持续失败")

    with org_session(ORG_A) as session:
        event = outbox_service.emit(
            session,
            ORG_A,
            aggregate_type=outbox_service.AGGREGATE_TRIP,
            aggregate_id=uuid.uuid4(),
            event_type="it.boom",
            consumers=(consumer,),
            max_attempts=2,
        )
    tracker["events"].append(event.id)
    event_id = event.id

    with org_session(ORG_A) as session:
        first = outbox_service.publish(session, consumer=consumer, handlers={"it.boom": boom})
    assert first.claimed == 1 and first.retried == 1 and first.failed == 0
    assert _delivery_status(event_id, consumer) == "PENDING"
    assert _event_status(event_id) == "PENDING"

    with org_session(ORG_A) as session:
        second = outbox_service.publish(session, consumer=consumer, handlers={"it.boom": boom})
    assert second.claimed == 0, "退避期内不该被再次认领"

    _make_event_ready_now(event_id)

    with org_session(ORG_A) as session:
        third = outbox_service.publish(session, consumer=consumer, handlers={"it.boom": boom})
    assert third.claimed == 1 and third.failed == 1
    assert event_id in third.dead_events
    assert _delivery_status(event_id, consumer) == "FAILED"
    assert _event_status(event_id) == "DEAD"


def test_publish_dead_letters_unregistered_event_type_immediately(tracker: dict[str, list]) -> None:
    consumer = _unique_consumer()
    with org_session(ORG_A) as session:
        event = outbox_service.emit(
            session,
            ORG_A,
            aggregate_type=outbox_service.AGGREGATE_TRIP,
            aggregate_id=uuid.uuid4(),
            event_type="it.nobody",
            consumers=(consumer,),
            max_attempts=3,
        )
    tracker["events"].append(event.id)
    event_id = event.id

    with org_session(ORG_A) as session:
        report = outbox_service.publish(session, consumer=consumer, handlers={})
    assert report.claimed == 1 and report.failed == 1 and report.retried == 0
    assert event_id in report.dead_events
    assert _delivery_status(event_id, consumer) == "FAILED"
    assert _event_status(event_id) == "DEAD"


# ---------------------------------------------- 消费者：真的改变出行状态
def test_trip_status_sync_moves_planning_trip_to_ready_then_back_to_draft(
    tracker: dict[str, list],
) -> None:
    with org_session(ORG_A) as session:
        trip_id = Trip(
            organization_id=ORG_A,
            name=f"集成测试·状态同步·{uuid.uuid4().hex[:8]}",
            home_timezone=HOME_TZ,
            currency="CNY",
            window_start=datetime(2026, 11, 9, 9, 0, tzinfo=SHANGHAI_TZ),
            window_end=datetime(2026, 11, 15, 18, 0, tzinfo=SHANGHAI_TZ),
            countries=[],
            status="PLANNING",
            version=1,
        )
        session.add(trip_id)
        session.flush()
        trip_uuid = trip_id.id
    tracker["trips"].append(trip_uuid)

    with org_session(ORG_A) as session:
        created = outbox_service.emit(
            session,
            ORG_A,
            aggregate_type=outbox_service.AGGREGATE_TRIP,
            aggregate_id=trip_uuid,
            event_type=outbox_service.EVENT_PLAN_VERSION_CREATED,
            payload={"trip_id": str(trip_uuid)},
            consumers=(outbox_service.CONSUMER_TRIP_STATUS_SYNC,),
            dedupe_key=f"it-plan-version-created.{uuid.uuid4().hex}",
        )
    tracker["events"].append(created.id)

    report = workers_main.publish_outbox(ORG_A, batch_size=None)
    assert report.done >= 1
    assert _trip_status(trip_uuid) == "READY"

    _set_trip_status(trip_uuid, "PLANNING")
    with org_session(ORG_A) as session:
        rejected = outbox_service.emit(
            session,
            ORG_A,
            aggregate_type=outbox_service.AGGREGATE_TRIP,
            aggregate_id=trip_uuid,
            event_type=outbox_service.EVENT_PLAN_NOT_FEASIBLE,
            payload={"trip_id": str(trip_uuid), "code": "PLAN_NOT_FEASIBLE"},
            consumers=(outbox_service.CONSUMER_TRIP_STATUS_SYNC,),
            dedupe_key=f"it-plan-not-feasible.{uuid.uuid4().hex}",
        )
    tracker["events"].append(rejected.id)

    report = workers_main.publish_outbox(ORG_A, batch_size=None)
    assert report.done >= 1
    assert _trip_status(trip_uuid) == "DRAFT"


# ----------------------------------------------- API：plans:generate 幂等
def test_plans_generate_is_idempotent_and_returns_job_handle(
    client: TestClient, tracker: dict[str, list]
) -> None:
    origin = _unique_place(client, tracker, city="Shanghai", country="CN", lat=31.2304, lon=121.4737,
                           tz=HOME_TZ)
    trip_id = _create_trip(client, tracker, origin_place_id=origin, name=f"集成测试·生成计划·{uuid.uuid4().hex[:8]}")

    key = f"it-plan-generate.{uuid.uuid4().hex}"
    tracker["idempotency"].append((PLANS_GENERATE_ENDPOINT, key))
    headers = {**_headers(), "Idempotency-Key": key}

    first = client.post(f"/v1/trips/{trip_id}/plans:generate", headers=headers)
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["trip_id"] == trip_id and body["trip_version"] == 1
    assert body["status"] == "PENDING" and body["reused"] is False
    job_id = body["job_id"]
    tracker["jobs"].append(UUID(job_id))

    second = client.post(f"/v1/trips/{trip_id}/plans:generate", headers=headers)
    assert second.status_code == 202
    assert second.content == first.content, "重放必须逐字节返回首次响应"
    assert second.headers.get("Idempotent-Replay") == "true"

    with org_session(ORG_A) as session:
        count = session.scalar(
            select(func.count())
            .select_from(BackgroundJob)
            .where(BackgroundJob.dedupe_key == f"plan_trip:{trip_id}:1")
        )
    assert count == 1, "重放不该多入一条任务"

    job = client.get(f"/v1/jobs/{job_id}", headers=_headers())
    assert job.status_code == 200
    assert job.json()["status"] == "PENDING"
    assert job.json()["job_type"] == "plan_trip"

    plans = client.get(f"/v1/trips/{trip_id}/plans", headers=_headers())
    assert plans.status_code == 200 and plans.json() == []

    assert _trip_status(UUID(trip_id)) == "PLANNING", "受理生成后出行应进入 PLANNING"


# ------------------------------------------------------ 端到端：拒绝
def test_end_to_end_rejects_trip_without_plannable_visit(
    client: TestClient, tracker: dict[str, list]
) -> None:
    origin = _unique_place(client, tracker, city="Shanghai", country="CN", lat=31.2304, lon=121.4737,
                           tz=HOME_TZ)
    trip_id = _create_trip(client, tracker, origin_place_id=origin, name=f"集成测试·无拜访·{uuid.uuid4().hex[:8]}")

    job_id = _start_generation(client, tracker, trip_id)
    assert _trip_status(UUID(trip_id)) == "PLANNING"

    worker_id = "it-worker-reject"
    _claim_by_id(UUID(job_id), worker_id)
    ok = workers_main.execute_one(ORG_A, job_id, worker_id=worker_id, lease_seconds=60)
    assert ok is False

    job = _job(UUID(job_id))
    assert job.status == "FAILED" and job.finished_at is not None
    assert job.last_error and "可规划" in job.last_error

    workers_main.publish_outbox(ORG_A, batch_size=None)
    assert _trip_status(UUID(trip_id)) == "DRAFT", "不可行应把出行退回 DRAFT，而不是永远停在 PLANNING"
    assert client.get(f"/v1/trips/{trip_id}/plans", headers=_headers()).json() == []


# ------------------------------------------------------ 端到端：成功
def test_end_to_end_generates_and_persists_plan(client: TestClient, tracker: dict[str, list]) -> None:
    origin = _unique_place(client, tracker, city="Shanghai", country="CN", lat=31.2304, lon=121.4737,
                           tz=HOME_TZ)
    berlin = _unique_place(client, tracker, city="Berlin", country="DE", lat=52.5200, lon=13.4050,
                           tz="Europe/Berlin")
    trip_id = _create_trip(client, tracker, origin_place_id=origin, name=f"集成测试·生成计划·{uuid.uuid4().hex[:8]}")
    visit_id = _add_visit(client, trip_id, place_id=berlin)

    job_id = _start_generation(client, tracker, trip_id)

    worker_id = "it-worker-plan"
    _claim_by_id(UUID(job_id), worker_id)
    ok = workers_main.execute_one(ORG_A, job_id, worker_id=worker_id, lease_seconds=60)
    assert ok is True, "求解与落库应当成功"

    job = _job(UUID(job_id))
    assert job.status == "SUCCEEDED"
    assert job.result is not None
    assert job.result["version_number"] == 1
    assert job.result["placed_visits"] == [visit_id]
    assert job.result["unplaced"] == []

    report = workers_main.publish_outbox(ORG_A, batch_size=None)
    assert report.done >= 1
    assert _trip_status(UUID(trip_id)) == "READY"

    plans = client.get(f"/v1/trips/{trip_id}/plans", headers=_headers())
    assert plans.status_code == 200, plans.text
    versions = plans.json()
    assert len(versions) == 1
    assert versions[0]["version_number"] == 1 and versions[0]["status"] == "DRAFT"
    assert versions[0]["validation"]["feasible"] is True

    detail = client.get(f"/v1/plan-versions/{versions[0]['id']}", headers=_headers())
    assert detail.status_code == 200, detail.text
    stops = detail.json()["stops"]
    assert len(stops) == 1 and str(stops[0]["visit_id"]) == visit_id

    visits = client.get(f"/v1/trips/{trip_id}/visits", headers=_headers())
    assert visits.status_code == 200
    matched = [item for item in visits.json() if item["id"] == visit_id]
    assert len(matched) == 1 and matched[0]["status"] == "SCHEDULED"

    # 一致性：目标值可由明细重算（travel*1.0 - value*0.001），不依赖引擎内部状态
    total_travel = sum(item["travel_minutes"] for item in stops)
    assert total_travel >= 0


# ===========================================================================
# 计划版本激活与差异（T8）
# 这一段要证明的是"不可变快照 + 乐观并发 + 部分唯一索引"三者合起来真的成立：
# 激活能原子换庄、能回滚、陈旧输入被拒、内容改不动。全部打真库。
# ===========================================================================
def _produce_plan_version(
    client: TestClient, tracker: dict[str, list], trip_id: str
) -> dict:
    """跑完整链路产出一版计划（入队 → 抢占 → 执行 → 消费 outbox），返回 job.result。

    走接口而不是直接调服务：这样"版本号递增由 persist_plan 在锁内计算"这件事是被真链路验证的。
    outbox 必须消费掉，出行才会从 ``PLANNING`` 进 ``READY``，后续激活才对得上真实状态。
    """
    job_id = _start_generation(client, tracker, trip_id)
    worker_id = f"it-worker-plan-{uuid.uuid4().hex[:6]}"
    _claim_by_id(UUID(job_id), worker_id)
    assert workers_main.execute_one(ORG_A, job_id, worker_id=worker_id, lease_seconds=60) is True, (
        "求解与落库应当成功"
    )
    job = _job(UUID(job_id))
    assert job.status == "SUCCEEDED", job.last_error
    workers_main.publish_outbox(ORG_A, batch_size=None)
    assert job.result is not None
    # 任务返回值刻意只带求解结论，不带 ``trip_version``（那是落库时在行锁内快照下来的）。
    # 用接口回读详情把两侧对齐，顺带验证 "GET plan-version 返回的 trip_version 就是快照值"。
    detail = client.get(f"/v1/plan-versions/{job.result['plan_version_id']}", headers=_headers())
    assert detail.status_code == 200, detail.text
    assert detail.json()["version_number"] == job.result["version_number"]
    return {**job.result, "trip_version": detail.json()["trip_version"]}


def _activate(
    client: TestClient,
    tracker: dict[str, list],
    trip_id: str,
    plan_version_id: str,
    *,
    if_match: int | None = None,
    key: str | None = None,
):
    """调用激活接口。``key`` 传了就记下来供收尾清理（幂等记录会留在库里）。"""
    idem = key or f"it-activate.{uuid.uuid4().hex}"
    tracker["idempotency"].append((PLANS_ACTIVATE_ENDPOINT, idem))
    headers = {**_headers(), "Idempotency-Key": idem}
    if if_match is not None:
        headers["If-Match"] = str(if_match)
    return client.post(
        f"/v1/trips/{trip_id}/plans/{plan_version_id}:activate", headers=headers
    )


def _program_versions(trip_id: str) -> list[PlanVersion]:
    """按版本号升序取出该出行的全部计划版本（校验生命周期字段用）。"""
    with org_session(ORG_A) as session:
        return list(
            session.scalars(
                select(PlanVersion)
                .where(PlanVersion.trip_id == UUID(trip_id))
                .order_by(PlanVersion.version_number)
            )
        )


def _activated_events(trip_id: str) -> list[OutboxEvent]:
    """该出行已发出的全部"计划被激活"事件，按发生顺序。"""
    with org_session(ORG_A) as session:
        return list(
            session.scalars(
                select(OutboxEvent)
                .where(
                    OutboxEvent.event_type == outbox_service.EVENT_PLAN_VERSION_ACTIVATED,
                    OutboxEvent.aggregate_id == UUID(trip_id),
                )
                .order_by(OutboxEvent.created_at)
            )
        )


def _activated_event(trip_id: str) -> OutboxEvent | None:
    events = _activated_events(trip_id)
    return events[-1] if events else None


def _trip_snapshot(trip_id: str) -> dict:
    with org_session(ORG_A) as session:
        trip = session.scalars(select(Trip).where(Trip.id == UUID(trip_id))).one()
        return {
            "status": trip.status,
            "version": trip.version,
            "active_plan_version_id": trip.active_plan_version_id,
        }


def test_plan_version_content_fields_partition_is_complete() -> None:
    """回归：新加列若忘了归类，会静默变成"可改字段"。这里把三份集合钉死。

    ``id`` 是主键，既不是内容也不是生命周期——它本来就不该被改，因此单独归一类。
    """
    columns = set(PlanVersion.__table__.columns.keys())
    content = set(PLAN_VERSION_CONTENT_FIELDS)
    assert content & LIFECYCLE_FIELDS == set(), "内容字段与生命周期字段不得重叠"
    assert content | LIFECYCLE_FIELDS | {"id"} == columns, (
        f"未归类的列：{sorted(columns - content - LIFECYCLE_FIELDS - {'id'})}；"
        f"已归类但不存在的列：{sorted((content | LIFECYCLE_FIELDS) - columns)}"
    )


def test_stop_delta_classifies_each_change() -> None:
    """差异判定的五个分支。纯内存对象，不落库——判定规则本身不该依赖数据库行为。"""
    visit = uuid.uuid4()
    base = datetime(2026, 11, 9, 1, 0, tzinfo=UTC)

    def stop(*, day: int = 0, seq: int = 0, travel: int = 0, service: int = 90, offset: int = 0) -> PlanStop:
        return PlanStop(
            visit_id=visit,
            day_index=day,
            sequence=seq,
            day_date=base.date(),
            arrive_at=base + timedelta(minutes=offset),
            depart_at=base + timedelta(minutes=offset + service),
            travel_minutes=travel,
            service_minutes=service,
        )

    # 只在目标版出现 ⇒ ADDED；只在基准版出现 ⇒ REMOVED。
    assert plan_change_service._stop_delta(None, stop()).change == "ADDED"
    assert plan_change_service._stop_delta(stop(), None).change == "REMOVED"
    # 位置（天/序）变了 ⇒ MOVED，优先级高于时刻（时刻变化是位置变化的结果，不单独再报）。
    assert plan_change_service._stop_delta(stop(day=0, seq=1), stop(day=1, seq=0)).change == "MOVED"
    # 位置不变、时刻或时长变了 ⇒ RETIMED。
    assert plan_change_service._stop_delta(stop(offset=0), stop(offset=30)).change == "RETIMED"
    assert plan_change_service._stop_delta(stop(travel=10), stop(travel=20)).change == "RETIMED"
    # 完全一致 ⇒ UNCHANGED。
    assert plan_change_service._stop_delta(stop(travel=10), stop(travel=10)).change == "UNCHANGED"
    # 缺失一侧的字段留 None，而不是塞 0 或哨兵值（客户端据此区分"没有"和"是 0"）。
    added = plan_change_service._stop_delta(None, stop())
    assert added.day_index_from is None and added.travel_minutes_from is None
    assert added.day_index_to == 0


def test_activate_creates_effective_plan_and_emits_audit_event(
    client: TestClient, tracker: dict[str, list]
) -> None:
    """激活一次：出行进 ACTIVE、计划进 ACTIVE、事件已发且**没有**消费者。"""
    origin = _unique_place(client, tracker, city="Shanghai", country="CN", lat=31.2304, lon=121.4737,
                           tz=HOME_TZ)
    berlin = _unique_place(client, tracker, city="Berlin", country="DE", lat=52.5200, lon=13.4050,
                           tz="Europe/Berlin")
    trip_id = _create_trip(client, tracker, origin_place_id=origin,
                           name=f"集成测试·激活·{uuid.uuid4().hex[:8]}")
    _add_visit(client, trip_id, place_id=berlin)

    result = _produce_plan_version(client, tracker, trip_id)
    plan_version_id = result["plan_version_id"]
    before = _trip_snapshot(trip_id)
    assert before["status"] == "READY" and before["active_plan_version_id"] is None

    resp = _activate(client, tracker, trip_id, plan_version_id, if_match=before["version"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["plan_version_id"] == plan_version_id
    assert body["status"] == "ACTIVE"
    assert body["superseded_plan_version_id"] is None
    assert body["trip_status"] == "ACTIVE"
    # 激活**不改** trips.version：version 追踪的是规划输入，不是哪一份计划生效。
    assert body["trip_version"] == before["version"] == body["plan_trip_version"]

    after = _trip_snapshot(trip_id)
    assert after["status"] == "ACTIVE"
    assert str(after["active_plan_version_id"]) == plan_version_id
    assert after["version"] == before["version"]

    versions = _program_versions(trip_id)
    assert [v.status for v in versions] == ["ACTIVE"]
    assert versions[0].activated_at is not None and versions[0].superseded_at is None

    event = _activated_event(trip_id)
    assert event is not None, "激活必须留下审计事件"
    tracker["events"].append(event.id)
    assert event.payload["plan_version_id"] == plan_version_id
    assert event.payload["superseded_plan_version_id"] is None
    assert event.payload["activated_by_user_id"] == USER_A
    # 激活事件只有一个消费者：主动式运行时。计划被激活是 §8.1 的 EVENT 触发源，
    # 它会立刻对该出行重扫状态规则（未排项/缺坐标），但不会改出行状态本身。
    with org_session(ORG_A) as session:
        consumers = list(
            session.scalars(
                select(OutboxDelivery.consumer).where(OutboxDelivery.event_id == event.id)
            )
        )
    assert consumers == ["proactive_trigger_eval"]


def test_activation_supersedes_previous_and_supports_rollback(
    client: TestClient, tracker: dict[str, list]
) -> None:
    """连续激活两版再回滚到旧版：``SUPERSEDED`` 可被重新激活，这是 RL-02 的立命之本。"""
    origin = _unique_place(client, tracker, city="Shanghai", country="CN", lat=31.2304, lon=121.4737,
                           tz=HOME_TZ)
    berlin = _unique_place(client, tracker, city="Berlin", country="DE", lat=52.5200, lon=13.4050,
                           tz="Europe/Berlin")
    trip_id = _create_trip(client, tracker, origin_place_id=origin,
                           name=f"集成测试·换庄·{uuid.uuid4().hex[:8]}")
    _add_visit(client, trip_id, place_id=berlin)

    # 两次生成：入队去重键是"出行 + 版本"，第一版跑完后同一 key 允许重新入队（旧任务已终态）。
    first = _produce_plan_version(client, tracker, trip_id)
    second = _produce_plan_version(client, tracker, trip_id)
    v1, v2 = first["plan_version_id"], second["plan_version_id"]
    assert first["version_number"] == 1 and second["version_number"] == 2

    trip_version = first["trip_version"]
    assert _activate(client, tracker, trip_id, v1, if_match=trip_version).status_code == 200

    swap = _activate(client, tracker, trip_id, v2, if_match=trip_version)
    assert swap.status_code == 200, swap.text
    assert swap.json()["superseded_plan_version_id"] == v1

    versions = {str(v.id): v for v in _program_versions(trip_id)}
    assert versions[v1].status == "SUPERSEDED" and versions[v1].superseded_at is not None
    assert versions[v2].status == "ACTIVE" and versions[v2].superseded_at is None

    rollback = _activate(client, tracker, trip_id, v1, if_match=trip_version)
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["superseded_plan_version_id"] == v2, "回滚应把当前生效版本顶下去"

    versions = {str(v.id): v for v in _program_versions(trip_id)}
    assert versions[v1].status == "ACTIVE"
    assert versions[v2].status == "SUPERSEDED"
    assert str(_trip_snapshot(trip_id)["active_plan_version_id"]) == v1

    # 部分唯一索引的兜底：任何时刻至多一行 ACTIVE（多行会直接违反约束，这里从结果侧断言）。
    active = [v for v in versions.values() if v.status == "ACTIVE"]
    assert len(active) == 1

    # 三次激活 = 三条审计事件。这一条同时是"回滚事件不能被去重键吞掉"的回归：
    # 若给激活事件加上 ``{trip}:{target}`` 形式的去重键，第三次（把 v1 再扶正）会命中第一次，
    # 事件数会掉到 2，且整段"顶下去又回来"的审计痕迹消失。
    events = _activated_events(trip_id)
    for event in events:
        tracker["events"].append(event.id)
    assert len(events) == 3, f"每次真实状态迁移都该留痕，实际 {len(events)} 条"
    assert len({event.created_at for event in events}) == 3, "三次激活发生在三个不同事务里"
    assert [
        (event.payload["plan_version_id"], event.payload["superseded_plan_version_id"])
        for event in events
    ] == [(v1, None), (v2, v1), (v1, v2)]


def test_activate_rejects_stale_precondition_duplicate_and_stale_input(
    client: TestClient, tracker: dict[str, list]
) -> None:
    """三类拒绝：``If-Match`` 不符 412、重复激活 409、基于旧输入的计划 409。"""
    origin = _unique_place(client, tracker, city="Shanghai", country="CN", lat=31.2304, lon=121.4737,
                           tz=HOME_TZ)
    berlin = _unique_place(client, tracker, city="Berlin", country="DE", lat=52.5200, lon=13.4050,
                           tz="Europe/Berlin")
    munich = _unique_place(client, tracker, city="Munich", country="DE", lat=48.1374, lon=11.5755,
                           tz="Europe/Berlin")
    rotterdam = _unique_place(client, tracker, city="Rotterdam", country="NL", lat=51.9244,
                              lon=4.4777, tz="Europe/Berlin")
    trip_id = _create_trip(client, tracker, origin_place_id=origin,
                           name=f"集成测试·拒绝·{uuid.uuid4().hex[:8]}")
    _add_visit(client, trip_id, place_id=berlin)

    result = _produce_plan_version(client, tracker, trip_id)
    plan_version_id = result["plan_version_id"]
    trip_version = result["trip_version"]

    # ① If-Match 不符：与 PATCH /trips 同语义（同一条件、同一补救动作），因此同为 412。
    stale = _activate(client, tracker, trip_id, plan_version_id, if_match=trip_version + 1)
    assert stale.status_code == 412, stale.text
    assert stale.json()["error"]["code"] == "PRECONDITION_FAILED"
    assert stale.json()["error"]["details"]["current_version"] == trip_version

    # ② 先成功激活一次，再对"同一条路径"点第二次：最准确的说法是"它已经生效了"。
    ok = _activate(client, tracker, trip_id, plan_version_id, if_match=trip_version)
    assert ok.status_code == 200, ok.text

    duplicate = _activate(client, tracker, trip_id, plan_version_id, if_match=trip_version)
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json()["error"]["code"] == "PLAN_VERSION_CONFLICT"
    assert duplicate.json()["error"]["details"]["reason"] == "ALREADY_ACTIVE"

    # ③ 陈旧计划：要造出"还没生效、但依据的输入已经过期"的版本。
    #    已生效的那一版走不到这个分支（ALREADY_ACTIVE 先拦下），所以这里另生一版：
    #    先加拜访让输入前进到 N+1 并基于它生成 v2，再加一个拜访把输入推到 N+2，此时 v2 才是陈旧的。
    _add_visit(client, trip_id, place_id=munich)
    changed = _trip_snapshot(trip_id)
    assert changed["version"] == trip_version + 1

    fresh = _produce_plan_version(client, tracker, trip_id)
    assert fresh["version_number"] == 2
    assert fresh["trip_version"] == trip_version + 1, "新版本应快照当前输入版本"

    _add_visit(client, trip_id, place_id=rotterdam)
    after = _trip_snapshot(trip_id)
    assert after["version"] == trip_version + 2

    stale_plan = _activate(client, tracker, trip_id, fresh["plan_version_id"], if_match=after["version"])
    assert stale_plan.status_code == 409, stale_plan.text
    assert stale_plan.json()["error"]["details"]["reason"] == "STALE_PLAN_VERSION"
    assert stale_plan.json()["error"]["details"]["plan_trip_version"] == trip_version + 1
    assert stale_plan.json()["error"]["details"]["current_trip_version"] == trip_version + 2

    # 被拒之后不得留下半成品：仍然是先前那一版在生效。
    assert str(_trip_snapshot(trip_id)["active_plan_version_id"]) == plan_version_id


def test_activate_rejects_unknown_version_and_foreign_trip(
    client: TestClient, tracker: dict[str, list]
) -> None:
    """404 与 422 的分工：不存在 ≠ 用错了。跨出行的计划版本是"用错"，不是"没找到"。"""
    origin = _unique_place(client, tracker, city="Shanghai", country="CN", lat=31.2304, lon=121.4737,
                           tz=HOME_TZ)
    berlin = _unique_place(client, tracker, city="Berlin", country="DE", lat=52.5200, lon=13.4050,
                           tz="Europe/Berlin")
    trip_a = _create_trip(client, tracker, origin_place_id=origin,
                          name=f"集成测试·错配A·{uuid.uuid4().hex[:8]}")
    trip_b = _create_trip(client, tracker, origin_place_id=origin,
                          name=f"集成测试·错配B·{uuid.uuid4().hex[:8]}")
    _add_visit(client, trip_a, place_id=berlin)
    _add_visit(client, trip_b, place_id=berlin)

    foreign = _produce_plan_version(client, tracker, trip_a)["plan_version_id"]

    missing = _activate(client, tracker, trip_b, str(uuid.uuid4()))
    assert missing.status_code == 404, missing.text
    assert missing.json()["error"]["code"] == "NOT_FOUND"

    mismatch = _activate(client, tracker, trip_b, foreign)
    assert mismatch.status_code == 422, mismatch.text
    assert mismatch.json()["error"]["code"] == "PLAN_TRIP_MISMATCH"


def test_activate_is_idempotent_under_same_key(
    client: TestClient, tracker: dict[str, list]
) -> None:
    """幂等重放：同一 key 再打一次不改变任何状态，且**字节级**回放首次响应。"""
    origin = _unique_place(client, tracker, city="Shanghai", country="CN", lat=31.2304, lon=121.4737,
                           tz=HOME_TZ)
    berlin = _unique_place(client, tracker, city="Berlin", country="DE", lat=52.5200, lon=13.4050,
                           tz="Europe/Berlin")
    trip_id = _create_trip(client, tracker, origin_place_id=origin,
                           name=f"集成测试·激活幂等·{uuid.uuid4().hex[:8]}")
    _add_visit(client, trip_id, place_id=berlin)

    result = _produce_plan_version(client, tracker, trip_id)
    plan_version_id = result["plan_version_id"]
    trip_version = result["trip_version"]

    key = f"it-activate-fixed.{uuid.uuid4().hex}"
    first = _activate(client, tracker, trip_id, plan_version_id, if_match=trip_version, key=key)
    assert first.status_code == 200, first.text
    replay = _activate(client, tracker, trip_id, plan_version_id, if_match=trip_version, key=key)
    assert replay.status_code == 200, replay.text
    assert replay.headers.get("Idempotent-Replay") == "true"
    assert replay.content == first.content

    # 重放不得产生第二次副作用：仍只有一个事件、出行仍指向同一版本。
    with org_session(ORG_A) as session:
        events = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.event_type == outbox_service.EVENT_PLAN_VERSION_ACTIVATED,
                OutboxEvent.aggregate_id == UUID(trip_id),
            )
        ).all()
    assert len(events) == 1, f"重放不该再发一次事件，实际 {len(events)} 条"
    tracker["events"].append(events[0].id)
    assert str(_trip_snapshot(trip_id)["active_plan_version_id"]) == plan_version_id


def test_diff_reports_added_and_unchanged_stops_against_base(
    client: TestClient, tracker: dict[str, list]
) -> None:
    """差异接口：以路径里那版为主语，``against`` 为基准。新增/未变必须分得清。"""
    origin = _unique_place(client, tracker, city="Shanghai", country="CN", lat=31.2304, lon=121.4737,
                           tz=HOME_TZ)
    berlin = _unique_place(client, tracker, city="Berlin", country="DE", lat=52.5200, lon=13.4050,
                           tz="Europe/Berlin")
    frankfurt = _unique_place(client, tracker, city="Frankfurt", country="DE", lat=50.1109,
                              lon=8.6821, tz="Europe/Berlin")
    trip_id = _create_trip(client, tracker, origin_place_id=origin,
                           name=f"集成测试·差异·{uuid.uuid4().hex[:8]}")
    _add_visit(client, trip_id, place_id=berlin)

    base = _produce_plan_version(client, tracker, trip_id)
    frankfurt_visit = _add_visit(client, trip_id, place_id=frankfurt)
    target = _produce_plan_version(client, tracker, trip_id)

    assert base["plan_version_id"] != target["plan_version_id"]
    assert target["version_number"] == base["version_number"] + 1
    assert target["trip_version"] == base["trip_version"] + 1, "加拜访应递增 trips.version"

    resp = client.get(
        f"/v1/trips/{trip_id}/plans/{target['plan_version_id']}:diff",
        headers=_headers(),
        params={"against": base["plan_version_id"]},
    )
    assert resp.status_code == 200, resp.text
    diff = resp.json()
    assert diff["base_plan_version_id"] == base["plan_version_id"]
    assert diff["target_plan_version_id"] == target["plan_version_id"]
    assert diff["base_version_number"] == base["version_number"]
    assert diff["target_version_number"] == target["version_number"]

    by_change: dict[str, list[dict]] = {}
    for stop in diff["stops"]:
        by_change.setdefault(stop["change"], []).append(stop)
    assert by_change.get("REMOVED") is None or by_change["REMOVED"] == [], "没有拜访被删，不该有 REMOVED"
    added = by_change.get("ADDED", [])
    assert [item["visit_id"] for item in added] == [frankfurt_visit], "新拜访应唯一地出现在 ADDED 里"
    assert added[0]["day_index_from"] is None and added[0]["day_index_to"] is not None
    assert diff["summary"]["added"] == 1 and diff["summary"]["removed"] == 0
    assert diff["summary"]["total"] == len(diff["stops"]) == 2

    # 合计口径可独立复算：逐条明细求和应等于顶层汇总（不依赖服务端内部累加）。
    # 基准侧求和要跳过 ``None``——ADDED 的停靠在基准版里不存在，没有"旧侧分钟数"可加。
    assert diff["travel_minutes_to"] == sum(
        item["travel_minutes_to"] for item in diff["stops"] if item["travel_minutes_to"] is not None
    )
    assert diff["travel_minutes_from"] == sum(
        item["travel_minutes_from"] for item in diff["stops"] if item["travel_minutes_from"] is not None
    )
    assert diff["travel_minutes_delta"] == diff["travel_minutes_to"] - diff["travel_minutes_from"]
    assert diff["objective_delta"] == pytest.approx(
        diff["objective_to"] - diff["objective_from"], abs=1e-6
    )

    # ``against`` 是必填查询参数：漏掉应当是校验失败，而不是静默比了个默认值。
    # 全站统一信封把请求形状错误（RequestValidationError）归为 400 ``VALIDATION_ERROR``
    # ——这是项目级约定，不是本端点自定的，见 ``app/api/errors.py`` 的校验处理器。
    missing = client.get(
        f"/v1/trips/{trip_id}/plans/{target['plan_version_id']}:diff", headers=_headers()
    )
    assert missing.status_code == 400, missing.text
    assert missing.json()["error"]["code"] == "VALIDATION_ERROR"
    assert ["query", "against"] == missing.json()["error"]["details"][0]["loc"]


def test_plan_snapshot_rejects_content_mutation_but_allows_lifecycle(
    client: TestClient, tracker: dict[str, list]
) -> None:
    """不可变守卫：内容字段与停靠整行改不动，生命周期字段放行。"""
    origin = _unique_place(client, tracker, city="Shanghai", country="CN", lat=31.2304, lon=121.4737,
                           tz=HOME_TZ)
    berlin = _unique_place(client, tracker, city="Berlin", country="DE", lat=52.5200, lon=13.4050,
                           tz="Europe/Berlin")
    trip_id = _create_trip(client, tracker, origin_place_id=origin,
                           name=f"集成测试·不可变·{uuid.uuid4().hex[:8]}")
    _add_visit(client, trip_id, place_id=berlin)

    result = _produce_plan_version(client, tracker, trip_id)
    plan_version_id = UUID(result["plan_version_id"])

    # ① 改内容字段 —— 拒。
    with org_session(ORG_A) as session:
        version = session.get(PlanVersion, plan_version_id)
        assert version is not None
        version.objective_value = version.objective_value + 1.0
        with pytest.raises(PlanVersionImmutable, match="objective_value"):
            session.flush()
        session.rollback()

    # ② 改停靠任意字段 —— 拒（PlanStop 整行都是内容，没有"允许变更"的字段）。
    with org_session(ORG_A) as session:
        stop = session.scalars(
            select(PlanStop).where(PlanStop.plan_version_id == plan_version_id)
        ).first()
        assert stop is not None
        stop.day_index = stop.day_index + 1
        with pytest.raises(PlanVersionImmutable):
            session.flush()
        session.rollback()

    # ③ 删除停靠 —— 拒。若允许，历史计划的时间表会凭空少一行，事后对比就再也对不上。
    with org_session(ORG_A) as session:
        stop = session.scalars(
            select(PlanStop).where(PlanStop.plan_version_id == plan_version_id)
        ).first()
        assert stop is not None
        session.delete(stop)
        with pytest.raises(PlanVersionImmutable):
            session.flush()
        session.rollback()

    # ④ 生命周期字段 —— 放行。禁止它会让"激活"无法在同一事务内完成。
    with org_session(ORG_A) as session:
        version = session.get(PlanVersion, plan_version_id)
        assert version is not None
        version.status = "SUPERSEDED"
        version.superseded_at = datetime.now(UTC)
        session.flush()
        session.rollback()