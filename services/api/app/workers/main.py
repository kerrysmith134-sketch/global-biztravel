"""后台 Worker 主循环：抢占任务、执行、结算，并顺带推进 Outbox。

一个 Worker 每轮对每个已知租户做三件事（设计文档 v2.0 第 10.3 / 10.4 章）：

1. **抢占并执行一条任务**——抢占与执行在**两个事务**里。抢占事务只做"改状态 + 写租约"
   然后立刻提交；执行事务持有任务行的行锁跑完整段业务逻辑。合成一个事务的后果是：长事务把
   任务行锁到结束，崩溃后连"租约已过期"都写不进去（''job_service'' 模块文档已说明）。
2. **恢复过期租约**——把"进程在续租前就死了"的任务放回待执行队列。
3. **投递 Outbox**——按消费者逐个 ``publish``，让"已发生的事实"变成对用户可见的状态变化。

租户枚举走 ``owner`` 连接：RLS 一律 ``FORCE``，应用角色看不到别的租户的 ``organizations``
行，因此跨租户只能由运维身份读一次租户清单，再逐个用**应用连接 + 绑定该租户**去干活。
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
from types import FrameType
from uuid import UUID

from sqlalchemy import select

from app.config import get_settings
from app.db import org_session, owner_session
from app.models.identity import Organization
from app.models.reliability import BackgroundJob
from app.models.workflow import WorkflowInstance
from app.services import (
    job_service,
    market_claim_verification_service,
    market_research_dag_service,
    market_source_extraction_service,
    memory_service,
    metrics_service,
    notification_service,
    outbox_service,
    proactive_service,
    receipt_storage_reconciliation_service,
    retention_service,
)
from app.workers.consumers import CONSUMER_HANDLERS
from app.workers.tasks import TASK_HANDLERS, TaskFailed

logger = logging.getLogger("app.workers.main")

#: 每个租户上次主动式扫描的单调时钟。只活在进程内：Worker 重启后立刻重扫一次是期望行为
#: （崩溃期间可能错过了触发），而扫描本身按去重键幂等，重复扫描不会重复提醒。
_last_proactive_scan: dict[str, float] = {}
#: 每个租户上次复盘扫描的单调时钟。复盘按 ``source_ref`` 幂等，节流只为省查询。
_last_memory_review: dict[str, float] = {}
#: 每个租户上次指标快照的单调时钟。
_last_metrics_snapshot: dict[str, float] = {}
#: 每个租户上次留存清理的单调时钟。
_last_retention: dict[str, float] = {}
#: 每个租户上次私有票据对象对账的单调时钟。
_last_receipt_reconciliation: dict[str, float] = {}


# ---------------------------------------------------------------------------
# 参数
# ---------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="app.workers.main", description="后台任务 Worker")
    parser.add_argument(
        "--worker-id",
        default=None,
        help="Worker 标识，写进任务行的 locked_by（默认 hostname:pid）",
    )
    parser.add_argument("--once", action="store_true", help="跑完一轮就退出（用于测试与一次性补算）")
    parser.add_argument("--poll-seconds", type=int, default=None, help="空闲时的轮询间隔秒数")
    parser.add_argument(
        "--org",
        action="append",
        default=None,
        metavar="ORG_ID",
        help="只处理指定组织，可重复；不给则读取全部租户",
    )
    parser.add_argument("--lease-seconds", type=int, default=None, help="任务租约秒数")
    parser.add_argument("--batch-size", type=int, default=None, help="每轮每个消费者最多投递多少条")
    parser.add_argument("--verbose", action="store_true", help="输出 DEBUG 日志")
    return parser.parse_args(argv)


def _resolve_worker_id(explicit: str | None) -> str:
    if explicit:
        return explicit
    import os
    import socket

    return f"{socket.gethostname()}:{os.getpid()}"


def list_organizations() -> list[str]:
    """列出全部租户 id。

    用 ``owner`` 连接：``organizations`` 表在迁移里以 ``force=False`` 建策略，所以运维身份
    能读到全量；应用身份只会看到自己那一行。
    """
    with owner_session() as session:
        return [str(row) for row in session.scalars(select(Organization.id).order_by(Organization.id))]


# ---------------------------------------------------------------------------
# 三步
# ---------------------------------------------------------------------------
def claim_one(org_id: str, *, worker_id: str, lease_seconds: int | None) -> str | None:
    """在自己的事务里抢占一条任务，返回任务 id（无任务返回 ``None``）。"""
    with org_session(org_id) as session:
        job = job_service.claim_next(session, worker_id=worker_id, lease_seconds=lease_seconds)
        return None if job is None else str(job.id)


def _wake_research_graph_for_job(session, org_id: str, job: BackgroundJob) -> None:
    """Wake active Workspace DAG executions after research inputs or an answer change."""
    payload = job.payload or {}
    workspace_id = payload.get("workspace_id")
    if not workspace_id and job.job_type == job_service.JOB_TYPE_RESEARCH_EXECUTE:
        plan_id = payload.get("plan_id")
        if plan_id:
            plan = session.scalars(
                select(WorkflowInstance).where(
                    WorkflowInstance.organization_id == org_id,
                    WorkflowInstance.id == str(plan_id),
                    WorkflowInstance.workflow_type == "research_plan",
                )
            ).one_or_none()
            if plan is not None:
                workspace_id = (plan.payload or {}).get("workspace_id")
    if not workspace_id:
        return
    try:
        parsed_workspace_id = UUID(str(workspace_id))
    except (TypeError, ValueError):
        logger.warning("研究任务 %s 包含无效 workspace_id，未唤醒 DAG", job.id)
        return
    market_research_dag_service.wake_workspace_executions(
        session,
        org_id,
        parsed_workspace_id,
    )


def execute_one(
    org_id: str, job_id: str, *, worker_id: str, lease_seconds: int | None
) -> bool:
    """在**另一个事务**里执行任务并结算；返回是否成功。

    进入前先 ``FOR UPDATE`` 锁住任务行：执行期间其它 Worker 的"租约过期回收"会因为
    ``SKIP LOCKED`` 跳过这一行，从而排除"同一个任务被两个进程同时执行"。
    进程真的崩了，行锁随连接断开释放，租约到期后任务照常被回收。
    """
    with org_session(org_id) as session:
        job = session.scalars(
            select(BackgroundJob).where(BackgroundJob.id == job_id).with_for_update()
        ).one_or_none()
        if job is None:
            logger.warning("任务 %s 抢占后已消失，跳过", job_id)
            return False
        if job.status != "RUNNING" or job.locked_by != worker_id:
            # 租约已被回收并转交（或被人工改判）：放弃执行，避免重复副作用。
            logger.warning("任务 %s 已不在本 Worker 名下（status=%s），跳过", job_id, job.status)
            return False

        job_type = job.job_type
        handler = TASK_HANDLERS.get(job_type)
        if handler is None:
            # 未注册的任务类型是配置问题，重试不会变好：直接终态失败，让它在运维界面上可见。
            job_service.fail(
                session, job_id, error=f"没有注册 job_type {job.job_type!r} 的处理器", retryable=False
            )
            logger.error("任务 %s 的类型 %s 没有处理器，已终态失败", job_id, job.job_type)
            return False

        try:
            result = handler(session, job)
            if job_type in {
                job_service.JOB_TYPE_MARKET_QUESTION_ANSWER,
                job_service.JOB_TYPE_RESEARCH_EXECUTE,
                job_service.JOB_TYPE_MARKET_PAGE_EXTRACT,
            }:
                _wake_research_graph_for_job(session, org_id, job)
        except TaskFailed as exc:
            if exc.retryable:
                session.rollback()
                failed_job = job_service.fail(session, job_id, error=exc.message, retryable=True)
                if (
                    job_type == job_service.JOB_TYPE_MARKET_QUESTION_ANSWER
                    and failed_job.status == "DEAD"
                ):
                    _wake_research_graph_for_job(session, org_id, job)
                if job_type == job_service.JOB_TYPE_MARKET_PAGE_EXTRACT and failed_job.status == "DEAD":
                    market_source_extraction_service.mark_retry_exhausted(
                        session, org_id, failed_job.id, error=exc.message
                    )
                if job_type == job_service.JOB_TYPE_MARKET_CLAIM_VERIFY and failed_job.status == "DEAD":
                    market_claim_verification_service.mark_job_failed(
                        session, org_id, failed_job.id, error=exc.message
                    )
                logger.warning("任务 %s 失败（可重试）：%s", job_id, exc.message)
            else:
                # 不可重试：保留处理器已经写入的副作用再结算。计划不可行时必须把
                # plan.not_feasible 事件留下，否则出行会永远停在 PLANNING。
                failed_job = job_service.fail(session, job_id, error=exc.message, retryable=False)
                if job_type == job_service.JOB_TYPE_MARKET_QUESTION_ANSWER:
                    _wake_research_graph_for_job(session, org_id, job)
                if job_type == job_service.JOB_TYPE_MARKET_CLAIM_VERIFY:
                    market_claim_verification_service.mark_job_failed(
                        session, org_id, job_id, error=exc.message
                    )
                logger.warning("任务 %s 失败（终态）：%s", job_id, exc.message)
            return False
        except Exception as exc:  # noqa: BLE001 —— 未预期异常按可重试处理，绝不能炸掉整个循环
            session.rollback()
            error_message = f"{type(exc).__name__}: {exc}"
            failed_job = job_service.fail(session, job_id, error=error_message, retryable=True)
            if (
                job_type == job_service.JOB_TYPE_MARKET_QUESTION_ANSWER
                and failed_job.status == "DEAD"
            ):
                _wake_research_graph_for_job(session, org_id, job)
            if job_type == job_service.JOB_TYPE_MARKET_PAGE_EXTRACT and failed_job.status == "DEAD":
                market_source_extraction_service.mark_retry_exhausted(
                    session, org_id, failed_job.id, error=error_message
                )
            if job_type == job_service.JOB_TYPE_MARKET_CLAIM_VERIFY and failed_job.status == "DEAD":
                market_claim_verification_service.mark_job_failed(
                    session, org_id, failed_job.id, error=error_message
                )
            logger.exception("任务 %s 未预期异常，已回滚并排入重试", job_id)
            return False

        job_service.complete(session, job_id, result=result)
        logger.info("任务 %s 完成：%s", job_id, result.get("version_number"))
        return True


def recover_leases(org_id: str, *, limit: int = 50) -> int:
    """回收租约过期的任务；答案 Agent 进入 DEAD 时唤醒父 DAG。"""
    with org_session(org_id) as session:
        recovered_ids = job_service.recover_expired_leases(session, limit=limit)
        if recovered_ids:
            dead_answer_jobs = session.scalars(
                select(BackgroundJob).where(
                    BackgroundJob.organization_id == org_id,
                    BackgroundJob.id.in_(recovered_ids),
                    BackgroundJob.job_type == job_service.JOB_TYPE_MARKET_QUESTION_ANSWER,
                    BackgroundJob.status == "DEAD",
                )
            ).all()
            for job in dead_answer_jobs:
                _wake_research_graph_for_job(session, org_id, job)
        return len(recovered_ids)


def publish_outbox(org_id: str, *, batch_size: int | None) -> outbox_service.PublishReport:
    """为当前租户的所有消费者各投递一批交付，返回合并后的报告。"""
    merged = outbox_service.PublishReport()
    with org_session(org_id) as session:
        for consumer, handlers in CONSUMER_HANDLERS.items():
            report = outbox_service.publish(
                session, consumer=consumer, handlers=dict(handlers), batch_size=batch_size
            )
            merged.claimed += report.claimed
            merged.done += report.done
            merged.retried += report.retried
            merged.failed += report.failed
            merged.dead_events.extend(report.dead_events)
            merged.errors.extend(f"{consumer}: {item}" for item in report.errors)
    return merged


def scan_proactive(org_id: str, *, force: bool = False) -> proactive_service.ScanReport | None:
    """按配置间隔扫描主动式规则；返回 ``None`` 表示本轮被节流跳过。

    节流是"扫库频率"，不是"提醒频率"：提醒是否打扰用户由服务里的冷却窗口与每日上限决定。
    两者分开，才能让扫描足够频繁（分钟级）而用户侧依然安静。
    """
    interval = max(get_settings().proactive_scan_interval_seconds, 0)
    now = time.monotonic()
    previous = _last_proactive_scan.get(org_id)
    if not force and previous is not None and now - previous < interval:
        return None
    _last_proactive_scan[org_id] = now
    with org_session(org_id) as session:
        return proactive_service.scan_organization(session, org_id)


def review_memory(org_id: str, *, force: bool = False) -> int | None:
    """为已完成但尚未复盘的出行生成记忆摘要；``None`` 表示本轮被节流跳过。"""
    interval = max(get_settings().memory_review_interval_seconds, 0)
    now = time.monotonic()
    previous = _last_memory_review.get(org_id)
    if not force and previous is not None and now - previous < interval:
        return None
    _last_memory_review[org_id] = now
    with org_session(org_id) as session:
        return memory_service.review_completed_trips(session, org_id)


def snapshot_metrics(org_id: str, *, force: bool = False) -> int | None:
    """写一份当前窗口的指标快照；``None`` 表示本轮被节流跳过。"""
    interval = max(get_settings().metrics_snapshot_interval_seconds, 0)
    now = time.monotonic()
    previous = _last_metrics_snapshot.get(org_id)
    if not force and previous is not None and now - previous < interval:
        return None
    _last_metrics_snapshot[org_id] = now
    with org_session(org_id) as session:
        return len(metrics_service.snapshot(session, org_id))


def apply_retention(org_id: str, *, force: bool = False) -> retention_service.RetentionReport | None:
    """按留存策略脱敏过期笔记与记忆；``None`` 表示本轮被节流跳过。"""
    interval = max(get_settings().retention_interval_seconds, 0)
    now = time.monotonic()
    previous = _last_retention.get(org_id)
    if not force and previous is not None and now - previous < interval:
        return None
    _last_retention[org_id] = now
    with org_session(org_id) as session:
        return retention_service.apply_retention(session, org_id)


def reconcile_receipts(
    org_id: str, *, force: bool = False
) -> receipt_storage_reconciliation_service.ReceiptReconciliationReport | None:
    """扫描当前租户的私有票据对象；清理默认关闭，且受双时钟宽限期保护。"""
    interval = max(get_settings().receipt_reconciliation_interval_seconds, 1)
    now = time.monotonic()
    previous = _last_receipt_reconciliation.get(org_id)
    if not force and previous is not None and now - previous < interval:
        return None
    try:
        with org_session(org_id) as session:
            report = receipt_storage_reconciliation_service.reconcile_receipt_storage(
                session, org_id
            )
    except Exception:
        # A failed storage listing/transaction should retry after a short backoff,
        # not suppress this tenant's next reconciliation for a full hour.
        retry_backoff = min(interval, 300)
        _last_receipt_reconciliation[org_id] = now - interval + retry_backoff
        raise
    if report.errors:
        retry_backoff = min(interval, 300)
        _last_receipt_reconciliation[org_id] = now - interval + retry_backoff
    else:
        _last_receipt_reconciliation[org_id] = now
    return report


def dispatch_notifications(
    org_id: str, *, limit: int = 50
) -> notification_service.DispatchReport:
    """投递待发通知；失败按次数上限转 FAILED。"""
    with org_session(org_id) as session:
        return notification_service.dispatch_pending(session, org_id, limit=limit)


# ---------------------------------------------------------------------------
# 循环
# ---------------------------------------------------------------------------
def run_cycle(
    orgs: list[str],
    *,
    worker_id: str,
    lease_seconds: int | None,
    batch_size: int | None,
    proactive_force: bool = False,
) -> bool:
    """对每个租户跑一轮；返回本轮是否做过任何事（用于决定要不要 sleep）。"""
    did_work = False
    for org_id in orgs:
        try:
            claimed = claim_one(org_id, worker_id=worker_id, lease_seconds=lease_seconds)
            if claimed is not None:
                did_work = True
                execute_one(org_id, claimed, worker_id=worker_id, lease_seconds=lease_seconds)
        except Exception:  # noqa: BLE001 —— 单租户故障不应让整个 Worker 退出
            logger.exception("组织 %s 的任务处理失败", org_id)

        try:
            recovered = recover_leases(org_id)
            if recovered:
                did_work = True
                logger.warning("组织 %s 回收了 %d 条过期租约任务", org_id, recovered)
        except Exception:  # noqa: BLE001
            logger.exception("组织 %s 的租约回收失败", org_id)

        try:
            report = publish_outbox(org_id, batch_size=batch_size)
            if report.claimed or report.dead_events:
                did_work = True
            if report.claimed:
                logger.info(
                    "组织 %s outbox：认领 %d 投递 %d 重试 %d 失败 %d",
                    org_id,
                    report.claimed,
                    report.done,
                    report.retried,
                    report.failed,
                )
        except Exception:  # noqa: BLE001
            logger.exception("组织 %s 的 outbox 投递失败", org_id)

        try:
            dispatched = dispatch_notifications(org_id, limit=batch_size or 50)
            if dispatched.claimed:
                did_work = True
                logger.info(
                    "组织 %s 通知：认领 %d 送达 %d 重试 %d 失败 %d",
                    org_id,
                    dispatched.claimed,
                    dispatched.sent,
                    dispatched.retried,
                    dispatched.failed,
                )
        except Exception:  # noqa: BLE001
            logger.exception("组织 %s 的通知投递失败", org_id)

        try:
            scan = scan_proactive(org_id, force=proactive_force)
            if scan is not None and scan.did_work:
                did_work = True
                logger.info(
                    "组织 %s 主动式扫描：出行 %d、新触发 %d、新建议 %d（推送 %d / 保留 %d / 抑制 %d）",
                    org_id,
                    scan.trips_scanned,
                    scan.triggers,
                    scan.suggestions,
                    scan.pushed,
                    scan.withheld,
                    scan.suppressed,
                )
            if scan is not None and scan.errors:
                logger.warning("组织 %s 主动式扫描有 %d 条规则失败", org_id, len(scan.errors))
        except Exception:  # noqa: BLE001
            logger.exception("组织 %s 的主动式扫描失败", org_id)

        try:
            reviewed = review_memory(org_id, force=proactive_force)
            if reviewed:
                did_work = True
                logger.info("组织 %s 复盘了 %d 次已完成出行", org_id, reviewed)
        except Exception:  # noqa: BLE001
            logger.exception("组织 %s 的记忆复盘失败", org_id)

        try:
            retention = apply_retention(org_id, force=proactive_force)
            if retention is not None and (
                retention.records_redacted or retention.summaries_redacted
            ):
                did_work = True
                logger.info(
                    "组织 %s 留存脱敏：记录 %d、记忆 %d",
                    org_id,
                    retention.records_redacted,
                    retention.summaries_redacted,
                )
        except Exception:  # noqa: BLE001
            logger.exception("组织 %s 的留存清理失败", org_id)

        try:
            receipt_report = reconcile_receipts(org_id, force=proactive_force)
            if receipt_report is not None:
                if (
                    receipt_report.orphans_discovered
                    or receipt_report.orphans_eligible
                    or receipt_report.objects_deleted
                    or receipt_report.delete_failures
                    or receipt_report.resolved
                    or receipt_report.missing
                ):
                    did_work = True
                logger.info(
                    "组织 %s 票据对象对账：扫描 %d、发现孤儿 %d、满足清理条件 %d、"
                    "删除 %d、删除失败 %d、恢复关联 %d、对象缺失 %d（清理启用=%s）",
                    org_id,
                    receipt_report.objects_scanned,
                    receipt_report.orphans_discovered,
                    receipt_report.orphans_eligible,
                    receipt_report.objects_deleted,
                    receipt_report.delete_failures,
                    receipt_report.resolved,
                    receipt_report.missing,
                    receipt_report.cleanup_enabled,
                )
                if receipt_report.errors:
                    logger.warning(
                        "组织 %s 票据对象对账有 %d 条存储错误",
                        org_id,
                        len(receipt_report.errors),
                    )
        except Exception:  # noqa: BLE001 —— 单租户存储问题不应让整个 Worker 退出
            logger.exception("组织 %s 的票据对象对账失败", org_id)

        try:
            snapshots = snapshot_metrics(org_id, force=proactive_force)
            if snapshots:
                did_work = True
                logger.info("组织 %s 写入 %d 条指标快照", org_id, snapshots)
        except Exception:  # noqa: BLE001
            logger.exception("组织 %s 的指标快照失败", org_id)
    return did_work


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = get_settings()
    worker_id = _resolve_worker_id(args.worker_id)
    poll_seconds = args.poll_seconds if args.poll_seconds is not None else settings.worker_poll_seconds
    fixed_orgs = [str(org) for org in (args.org or [])]

    stop = False

    def _on_signal(signum: int, frame: FrameType | None) -> None:  # noqa: ARG001
        nonlocal stop
        logger.info("收到信号 %s，当前一轮结束后退出", signum)
        stop = True

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    logger.info("Worker %s 启动：轮询 %ss、租约 %ss", worker_id, poll_seconds, args.lease_seconds)

    while not stop:
        orgs = fixed_orgs or list_organizations()
        if not orgs:
            logger.warning("没有任何租户，跳过本轮")
        did_work = run_cycle(
            orgs,
            worker_id=worker_id,
            lease_seconds=args.lease_seconds,
            batch_size=args.batch_size,
            proactive_force=args.once,
        )
        if args.once:
            break
        if not did_work:
            # 只在完全空闲时 sleep：有活干就立刻再抢一条，避免把吞吐量钉在轮询间隔上。
            time.sleep(max(poll_seconds, 1))

    logger.info("Worker %s 退出", worker_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())