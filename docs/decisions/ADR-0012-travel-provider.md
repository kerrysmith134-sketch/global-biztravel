# ADR-0012：交通估算 Provider 与 stale/未知费用语义

- 状态：已接受（Accepted）
- 日期：2026-10-28
- 相关文件：`app/integrations/travel.py`、`app/services/planning_service.py`、`packages/planning-engine/travel_planner/{contracts,scheduling,validator}.py`、迁移 `0015_plan_travel_legs`
- 相关需求：PRD §5.1/5.2、AT-003；设计 §14

## 背景

MVP-1 的输出要求包含交通段、时间矩阵、费用分项；Provider 超时时必须降级、展示受影响段，
且"未知费用"不能显示为 0。此前引擎直接用大圆距离估算，没有 Provider 边界、没有费用、
没有 stale 标记。

## 决策

1. `PlanningProblem.travel_estimates` 接收逐段估算（起点/终点/模式/分钟/距离/费用/来源/stale）；
   调度器、OR-Tools 模型与独立验证器共用同一份估算；缺失时回退几何估算。
2. API 侧 `TravelProvider` 负责把供应商数据转成估算；默认 `offline-haversine`。
3. 供应商失败：重试一次；仍失败 ⇒ 回退离线估算，所有段 `stale=true`、
   `source=fallback-haversine`、费用 `None`；计划照常生成但 `degraded=true`。
4. 交通段落 `plan_travel_legs`，起终点是 `plan_stops` 真实外键；费用未知写 NULL，
   `cost_unknown_count` 单独计数，绝不写 0。
5. 求解状态 `PARTIAL/FEASIBLE` 与 `degraded` 分离：状态表达结果完整性，degraded 表达降级来源。

## 后果

- 好处：Provider 可替换；AT-003 可验证；费用语义诚实。
- 代价：真实供应商鉴权/字段映射仍待接入；离线估算的绝对时间不代表真实航班。
