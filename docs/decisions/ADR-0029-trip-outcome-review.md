# ADR-0029：出差结果复盘是只读聚合，"机会"一律待确认

- 状态：已接受（Accepted）
- 日期：2026-11-02
- 相关文件：`app/services/trip_outcome_service.py`、`app/api/routes/trips.py`（`GET /v1/trips/{id}/outcome`）、
  `app/api/schemas.py`（`TripOutcomeRead`）
- 相关需求：PRD P1「出差结果分析及客户机会复盘」、页面 07 §8.2 "不得把 AI 推断直接写入 CRM"

## 背景

PRD 要求对一次出差做结果分析（计划 vs 实际）并复盘客户机会。此前复盘只有记忆摘要
（`trip_memory_summaries`），没有把拜访结果、费用计划 vs 实际、纪要分类洞察聚合成一份
"这次出差结果如何、产生了哪些机会"的视图；而且商机信号源自 AI 推断，必须标记"待确认"。

## 决策

1. **只读聚合，不建新表**：`GET /v1/trips/{id}/outcome` 实时聚合已有数据——
   `visits.status` 计数、`visit_records.outcome` 分布、生效计划的 `cost_*`、
   `trip_expenses` 按币种分列、纪要洞察（`agent_runs.output.insights`）按类别计数、
   复盘记忆摘要引用。
2. **费用口径与预算汇总一致**：计划估算与实际费用按币种分列，不做汇率折算。
3. **机会一律待确认**：商机信号/交易条件/商业承诺/阶段变化随原文附上，每条
   `requires_confirmation=True`（服务端写死，不交给模型）——PRD：不得当作已确认事实。
4. **零 CRM 副作用**：复盘不写 CRM；测试断言全程零 CRM 同步任务。
5. 洞察经 `agent_runs`（纪要草稿输出）经 `input.visit_id` 关联到行程拜访，只取
   `SUCCEEDED` 的最近 100 次。

## 后果

- 好处：P1 的结果分析/机会复盘开箱可用；机会与"待确认"绑定，不会把 AI 推断当事实；
  只读聚合无迁移成本。
- 代价：洞察查询依赖 `agent_runs`（非索引关联），数据量大时需加索引或物化；费用折算
  仍缺（与预算汇总同口径，接入真实汇率后统一补）。
