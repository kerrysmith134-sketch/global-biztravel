"""后台 Worker：把队列里的任务真正跑起来（设计文档 v2.0 第 10.3 / 10.4 章）。

三个子模块分工：

- ``tasks``：任务处理器注册表。一个 ``job_type`` 对应一个函数，函数签名是
  ``(Session, BackgroundJob) -> dict``，返回值落进 ``background_jobs.result``。
- ``consumers``：Outbox 消费者处理器注册表。以 ``event_type`` 为键，因为同一消费者可能
  订阅多种事件（``trip_status_sync`` 就要同时处理"计划生成成功"和"计划不可行"两种走向）。
- ``main``：轮询循环。负责跨租户枚举、抢占、执行、结算，以及租约恢复。

「抢占」与「执行」为什么必须分两个事务：抢占本身是"把 ``PENDING`` 改成 ``RUNNING`` 并写租约"，
它要尽可能短；执行可能有几十秒。若在同一个事务里做，行锁会一直持有到执行结束——崩溃时连
"租约已过期"都写不进去（因为那条 UPDATE 也在同一个未提交事务里），任务就永久卡在 ``RUNNING``。
因此本包在抢占后**立刻 commit**，执行阶段用新事务。
"""

from __future__ import annotations

__all__ = ["consumers", "main", "tasks"]