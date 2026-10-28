"""Outbox 消费者：把"领域里发生了什么"翻译成"状态该怎么变"。

分工（设计文档 v2.0 第 10.4 章）：

- ``outbox_service.emit`` 只负责**记事实**：某个聚合根上发生了某件事，附带载荷。
- 这里负责**解释事实**：同一个事件可以被多个消费者各自理解，互不干扰。
- 于是"生成计划"这条链路里，任务处理器不必知道 Trip 状态机长什么样，只管发事件；
  真正改状态的逻辑集中在消费者里，可单独测试、可单独演进而不用重新求解。

消费者约定：

- **必须幂等**。事件可能被投递多次（重试、租约回收后重跑），因此每个处理器都要能
  "再看一遍已经成立的事实"而不产生第二次副作用。这里的做法是先看当前状态再决定是否
  改写，且改写是**幂等赋值**（置为 ``READY`` 而不是"递增"或"追加"）。
- **不递增 Trip 版本号**。状态由后台流程推进，不是用户改内容；若这里动 ``touch_trip``，
  刚生成的计划在激活时会因为 ``trip_version`` 对不上而被判冲突（必然 409）。
- **未注册的事件类型一律不登记**：``publish`` 把"找不到处理器"当作不可重试失败并终结该
  交付；反过来，如果为了"看起来完整"把没实现的类型塞进注册表，失败就会被静默吞掉。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.proactive import ProactiveSuggestion
from app.models.reliability import OutboxEvent
from app.models.trip import Trip
from app.services import notification_service, proactive_service
from app.services import outbox_service as box

ConsumerHandler = Callable[[Session, OutboxEvent], None]


def sync_trip_status(session: Session, event: OutboxEvent) -> None:
    """按事件把 Trip 的状态推进到该有的位置。

    - ``plan.version.created``：计划已落成候选版本 ⇒ 出行可进入 ``READY``（有可用计划）。
    - ``plan.not_feasible``：这次规划没能产出计划 ⇒ 出行回到 ``DRAFT``，让用户看到
      "还没规划好"而不是永远停在"正在规划中"。

    两个分支都只在**当前确实处于中间态**时改写：``PLANNING`` 是"正在算"的独占状态，
    若用户在这期间已经手动把它推进到别处（例如取消），我们不应该把它拨回去。
    再跑一次时判断不再成立，于是天然幂等。
    """
    trip = session.scalars(
        select(Trip).where(Trip.id == event.aggregate_id).with_for_update()
    ).one_or_none()
    if trip is None:
        # 出行已被删除：事件失去目标。不是错误，直接算消费完成。
        return

    if event.event_type == box.EVENT_PLAN_VERSION_CREATED:
        if trip.status == "PLANNING" and trip.active_plan_version_id is None:
            trip.status = "READY"
        return

    if event.event_type == box.EVENT_PLAN_NOT_FEASIBLE:
        if trip.status == "PLANNING":
            trip.status = "DRAFT"
        return


def evaluate_proactive_on_plan_event(session: Session, event: OutboxEvent) -> None:
    """计划激活后立刻重扫该出行的状态规则（设计文档 §8.1 的 EVENT 触发源）。

    为什么由消费者做、而不是在激活用例里直接调用：激活是同步、短事务；主动式扫描会读写
    多张表。把扫描放到 Outbox 后面，激活请求的延迟与失败面都不受影响。代价是建议会晚一个
    投递周期出现——对"出发前提醒"这类分钟级场景完全可接受。

    幂等：扫描本身按 ``(rule, dedupe_key)`` 去重，重复投递不会产生第二条建议。
    """
    payload = event.payload or {}
    trip_id = payload.get("trip_id") or event.aggregate_id
    proactive_service.scan_trip(
        session,
        event.organization_id,
        trip_id,
        trigger_type=proactive_service.TRIGGER_EVENT,
    )


def enqueue_notification(session: Session, event: OutboxEvent) -> None:
    """建议已提出 → 若已推送则创建通知。重复投递由通知唯一约束兜底。"""
    payload = event.payload or {}
    suggestion_id = payload.get("suggestion_id")
    if not suggestion_id:
        return
    suggestion = session.scalars(
        select(ProactiveSuggestion).where(
            ProactiveSuggestion.organization_id == event.organization_id,
            ProactiveSuggestion.id == UUID(str(suggestion_id)),
        )
    ).one_or_none()
    if suggestion is None:
        return
    trip = session.get(Trip, suggestion.trip_id)
    notification_service.enqueue_for_suggestion(
        session,
        suggestion,
        user_id=trip.owner_user_id if trip is not None else None,
    )


#: 消费者 → {事件类型: 处理器}。新增消费者时在这里登记，``main`` 会自动按消费者名逐个轮询。
CONSUMER_HANDLERS: Mapping[str, Mapping[str, ConsumerHandler]] = {
    box.CONSUMER_TRIP_STATUS_SYNC: {
        box.EVENT_PLAN_VERSION_CREATED: sync_trip_status,
        box.EVENT_PLAN_NOT_FEASIBLE: sync_trip_status,
    },
    box.CONSUMER_PROACTIVE_TRIGGER: {
        box.EVENT_PLAN_VERSION_ACTIVATED: evaluate_proactive_on_plan_event,
    },
    box.CONSUMER_NOTIFICATION_DISPATCH: {
        box.EVENT_SUGGESTION_PROPOSED: enqueue_notification,
    },
}

__all__ = [
    "CONSUMER_HANDLERS",
    "ConsumerHandler",
    "enqueue_notification",
    "evaluate_proactive_on_plan_event",
    "sync_trip_status",
]