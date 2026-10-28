"""事务外盒（Outbox）：让"状态变更"和"通知下游"在同一个事务里落库，且崩溃后可恢复。

设计文档 v2.0 第 10.4 章的要点与这里的实现一一对应：

- **为什么要有 outbox**：计划生成成功后要"把 Trip 推到 READY"。如果直接在任务处理器里改
  Trip 状态，就把"算计划"和"改状态"耦合成一次不可分割的动作——物理上确实是同一事务，但
  一旦以后要加第二个消费者（发通知、写审计、推指标），每加一个就得改一次任务处理器。
  Outbox 把"发生了什么"（事件）与"谁关心"（投递）分开：任务处理器只负责 ``emit``，
  消费由 ``publish`` 独立驱动。
- **至少一次投递**：``publish`` 崩溃或失败后会重试，因此同一交付可能被投递多次。
  这不是缺陷，而是必须被消费端吸收的契约——所有 handler **必须幂等**（见 ``workers/consumers.py``）。
- **两级重试**：交付级 ``OutboxDelivery.attempts`` 统计"这个消费者连续失败了几次"，
  事件级 ``OutboxEvent.next_attempt_at`` 负责给重试之间留出退避间隔。任一级用尽次数即终态。
- **死信**：只要有一个交付进入 ``FAILED``，事件就是 ``DEAD``——因为它是**终态失败**，
  继续重试不会变好（没注册处理器是配置错误，处理器耗尽次数说明对方一直报错）。
- **卡在 PROCESSING 的事件**：进程在"抢占后、结算前"被杀会留下这种事件。``publish`` 每轮
  先做一次扫尾（``_sweep_stalled_events``）：只要它的交付里已无 ``PENDING``，就按现有结果
  结算掉，避免事件永远悬空。

事务边界（``app/services/__init__.py`` 的约定）：本模块**不提交事务**，只 ``flush``。
调用方决定提交点——这样才能让 ``emit`` 与业务写入真正处在同一个事务里。
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models.reliability import OutboxDelivery, OutboxEvent

#: 聚合根类型。事件按聚合根归类，便于"某次出行发生过的所有事件"这类排查。
AGGREGATE_TRIP = "trip"

#: 计划生成成功：一个不可变 PlanVersion 已落库。
EVENT_PLAN_VERSION_CREATED = "plan.version.created"
#: 计划不可行：求解器与兜底都拿不出可行解，需要人来处理。
EVENT_PLAN_NOT_FEASIBLE = "plan.not_feasible"
#: 计划版本被激活：某份不可变快照被扶正为出行的生效计划。
#: 价值在审计与回放，同时作为主动式运行时的 EVENT 触发源（设计文档 §8.1 把"计划版本被激活"
#: 列为 EVENT 触发源）：消费者会立刻对该出行重扫一遍状态规则（未排项、缺坐标等）。
EVENT_PLAN_VERSION_ACTIVATED = "plan.version.activated"
#: 主动建议已提出：通知消费者据此创建推送记录（第 8.6 章）。
EVENT_SUGGESTION_PROPOSED = "proactive.suggestion.proposed"

#: 消费者名。命名对应"它负责的那件事"，而不是"它被哪个事件触发"。
CONSUMER_TRIP_STATUS_SYNC = "trip_status_sync"
CONSUMER_PROACTIVE_TRIGGER = "proactive_trigger_eval"
CONSUMER_NOTIFICATION_DISPATCH = "notification_dispatch"

#: 去重键在 payload 里的保留字段。用下划线前缀表明它由 outbox 自己管理，不是业务数据。
DEDUPE_FIELD = "_dedupe_key"

Handler = Callable[[Session, OutboxEvent], None]


@dataclass
class PublishReport:
    """一轮 ``publish`` 的结果，用于 Worker 日志与集成测试断言。"""

    claimed: int = 0
    done: int = 0
    retried: int = 0
    failed: int = 0
    dead_events: list[UUID] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def emit(
    session: Session,
    organization_id: UUID | str,
    *,
    aggregate_type: str,
    aggregate_id: UUID | str,
    event_type: str,
    payload: dict[str, Any] | None = None,
    consumers: Iterable[str] = (),
    max_attempts: int | None = None,
    dedupe_key: str | None = None,
) -> OutboxEvent:
    """在**当前事务内**记下一个事件，并为每个消费者建一条交付。

    去重语义分两层，必须分清楚：

    1. **交付级（强一致）**：``ON CONFLICT (event_id, consumer) DO NOTHING`` + 唯一约束
       ``uq_outbox_deliveries_event_id_consumer``。同一事件对同一消费者**不可能**出现两条交付，
       并发下也不会。这是"不重复消费"的硬保障。
    2. **事件级（尽力而为）**：传 ``dedupe_key`` 时先查同聚合根、同类型、仍在 ``PENDING``/
       ``PROCESSING`` 的事件；命中就复用而不新建。它防的是"同一个逻辑事件被重复发出"
       （例如任务被重试后重跑 emit）。这里刻意不引入额外唯一索引：要把它变成强一致需要
       给 ``outbox_events`` 加列并改部分唯一索引，而在当前流程里，真正需要强一致的是交付级
       去重，事件级重复最多导致消费者被多投一次——而消费者本来就要求幂等。
    """
    settings = get_settings()
    body = dict(payload or {})
    if dedupe_key is not None:
        body[DEDUPE_FIELD] = dedupe_key

    event = _find_dedupe_hit(session, organization_id, aggregate_type, aggregate_id, event_type, dedupe_key)
    if event is None:
        event = OutboxEvent(
            organization_id=str(organization_id),
            aggregate_type=aggregate_type,
            aggregate_id=str(aggregate_id),
            event_type=event_type,
            payload=body,
            status="PENDING",
            attempts=0,
            max_attempts=max_attempts if max_attempts is not None else settings.max_attempts,
            next_attempt_at=func.clock_timestamp(),
        )
        session.add(event)
        session.flush()

    for consumer in consumers:
        session.execute(
            pg_insert(OutboxDelivery.__table__)
            .values(
                organization_id=str(organization_id),
                event_id=event.id,
                consumer=consumer,
                status="PENDING",
                attempts=0,
            )
            .on_conflict_do_nothing(constraint="uq_outbox_deliveries_event_id_consumer")
        )
    session.flush()
    return event


def publish(
    session: Session,
    *,
    consumer: str,
    handlers: Mapping[str, Handler],
    batch_size: int | None = None,
) -> PublishReport:
    """为某个消费者投递一批待处理交付，并结算对应事件。

    每个 handler 都在一个 ``SAVEPOINT``（``session.begin_nested()``）里跑。这样"某个消费者
    处理失败"只回滚它自己的副作用，不会把同批次里其它交付、也不会把调用方尚未提交的业务写入
    一起带走——否则一条坏交付就能让整个批次原地空转。
    """
    settings = get_settings()
    limit = batch_size if batch_size is not None else settings.outbox_batch_size
    report = PublishReport()

    _sweep_stalled_events(session, report=report, limit=limit)

    claimed = _claim_deliveries(session, consumer=consumer, limit=limit)
    report.claimed = len(claimed)

    touched: list[OutboxEvent] = []
    for delivery, event in claimed:
        if event.status == "PENDING":
            event.status = "PROCESSING"
        handler = handlers.get(event.event_type)
        if handler is None:
            _record_failure(
                delivery,
                event,
                message=f"没有注册事件类型 {event.event_type!r} 的消费者处理器",
                retryable=False,
            )
        else:
            try:
                with session.begin_nested():
                    handler(session, event)
            except Exception as exc:  # noqa: BLE001 —— 消费者抛什么都要转成交付失败，不能炸掉整个批次
                _record_failure(delivery, event, message=f"{type(exc).__name__}: {exc}", retryable=True)
            else:
                delivery.status = "DONE"
                delivery.last_error = None
                delivery.delivered_at = session.scalar(select(func.clock_timestamp()))
                report.done += 1
        touched.append(event)

    session.flush()
    _settle_events(session, touched, settings=settings, report=report)
    session.flush()
    return report


# ---------------------------------------------------------------------------
# 内部：抢占
# ---------------------------------------------------------------------------
def _claim_deliveries(
    session: Session, *, consumer: str, limit: int
) -> list[tuple[OutboxDelivery, OutboxEvent]]:
    """抢占一批待投递交付。

    只锁 ``outbox_deliveries`` 行（``FOR UPDATE OF outbox_deliveries SKIP LOCKED``）：同一事件
    可能同时被多个消费者认领，锁住事件行会让不同消费者互相阻塞。
    ``OutboxEvent.status`` 允许 ``PROCESSING``——那是上一次崩在半路留下的，应该被继续推进。
    """
    rows = session.execute(
        select(OutboxDelivery, OutboxEvent)
        .join(OutboxEvent, OutboxDelivery.event_id == OutboxEvent.id)
        .where(
            OutboxDelivery.consumer == consumer,
            OutboxDelivery.status == "PENDING",
            OutboxEvent.status.in_(("PENDING", "PROCESSING")),
            OutboxEvent.next_attempt_at <= func.clock_timestamp(),
        )
        .order_by(OutboxEvent.created_at, OutboxDelivery.id)
        .with_for_update(skip_locked=True, of=OutboxDelivery)
        .limit(limit)
    ).all()
    return [(delivery, event) for delivery, event in rows]


def _record_failure(
    delivery: OutboxDelivery, event: OutboxEvent, *, message: str, retryable: bool
) -> None:
    delivery.last_error = message
    if not retryable:
        delivery.status = "FAILED"
        return
    delivery.attempts += 1
    if delivery.attempts >= event.max_attempts:
        delivery.status = "FAILED"
    else:
        delivery.status = "PENDING"


# ---------------------------------------------------------------------------
# 内部：结算
# ---------------------------------------------------------------------------
def _settle_events(
    session: Session,
    events: list[OutboxEvent],
    *,
    settings: Any,
    report: PublishReport,
    note: str | None = None,
) -> None:
    for event in events:
        _settle_event(session, event, settings=settings, report=report, note=note)


def _settle_event(
    session: Session,
    event: OutboxEvent,
    *,
    settings: Any,
    report: PublishReport,
    note: str | None,
) -> None:
    # session 是 autoflush=False：先把上面刚改的交付状态刷下去，否则下面 SELECT 读到的还是旧值。
    session.flush()
    statuses = set(
        session.scalars(
            select(OutboxDelivery.status).where(OutboxDelivery.event_id == event.id)
        ).all()
    )
    now = session.scalar(select(func.clock_timestamp()))

    if "FAILED" in statuses:
        if event.status != "DEAD":
            event.status = "DEAD"
            event.last_error = note or "存在投递失败的消费者，事件转入死信"
            event.processed_at = now
            report.dead_events.append(event.id)
        report.failed += 1
        return

    if "PENDING" in statuses:
        event.attempts += 1
        event.last_error = note
        if event.attempts >= event.max_attempts:
            event.status = "DEAD"
            event.processed_at = now
            report.dead_events.append(event.id)
            report.failed += 1
        else:
            event.status = "PENDING"
            event.next_attempt_at = now + _backoff(event.attempts, settings=settings)
            report.retried += 1
        return

    event.status = "DONE"
    event.last_error = None
    event.processed_at = now


def _sweep_stalled_events(session: Session, *, report: PublishReport, limit: int) -> None:
    """结算"卡在 PROCESSING 且已无待投递交付"的事件。

    这是崩溃恢复的收尾动作：进程若在抢占事件之后、结算之前被杀，事件就会停在 ``PROCESSING``。
    它不会自动变回来，因为结算只会发生在 ``publish`` 的当轮循环里。不扫的话，这些事件的
    交付即使都已经 ``DONE``，事件也永远不 ``DONE``，任何按事件状态做后续判断的逻辑都会卡住。
    """
    stalled = session.scalars(
        select(OutboxEvent)
        .where(
            OutboxEvent.status == "PROCESSING",
            ~select(OutboxDelivery.id)
            .where(
                OutboxDelivery.event_id == OutboxEvent.id,
                OutboxDelivery.status == "PENDING",
            )
            .exists(),
        )
        .order_by(OutboxEvent.updated_at)
        .with_for_update(skip_locked=True)
        .limit(limit)
    ).all()
    if not stalled:
        return
    _settle_events(
        session,
        list(stalled),
        settings=get_settings(),
        report=report,
        note="事件卡在 PROCESSING（进程在结算前退出），已按现有交付结果收尾",
    )


def _backoff(attempts: int, *, settings: Any) -> timedelta:
    seconds = settings.retry_base_seconds * 2 ** max(attempts - 1, 0)
    return timedelta(seconds=min(settings.retry_max_seconds, seconds))


# ---------------------------------------------------------------------------
# 内部：查询助手
# ---------------------------------------------------------------------------
def _find_dedupe_hit(
    session: Session,
    organization_id: UUID | str,
    aggregate_type: str,
    aggregate_id: UUID | str,
    event_type: str,
    dedupe_key: str | None,
) -> OutboxEvent | None:
    if dedupe_key is None:
        return None
    return session.scalars(
        select(OutboxEvent)
        .where(
            OutboxEvent.organization_id == str(organization_id),
            OutboxEvent.aggregate_type == aggregate_type,
            OutboxEvent.aggregate_id == str(aggregate_id),
            OutboxEvent.event_type == event_type,
            OutboxEvent.status.in_(("PENDING", "PROCESSING")),
            OutboxEvent.payload.op("->>")(DEDUPE_FIELD) == dedupe_key,
        )
        .order_by(OutboxEvent.created_at.desc())
        .limit(1)
    ).first()


__all__ = [
    "AGGREGATE_TRIP",
    "CONSUMER_TRIP_STATUS_SYNC",
    "DEDUPE_FIELD",
    "EVENT_PLAN_NOT_FEASIBLE",
    "EVENT_PLAN_VERSION_CREATED",
    "Handler",
    "PublishReport",
    "emit",
    "publish",
]