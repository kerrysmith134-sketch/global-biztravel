# 主动式运行时验收（AT-P01 ~ AT-P04 + STATE）

对应设计文档 v2.0 第 16.3 章。全部用例打真实 PostgreSQL（容器 `zhi-tu-pg`），走
FastAPI 应用栈、RLS 会话与真实服务层，不使用模拟 API 或预置演示数据。

测试文件：`services/api/tests/test_proactive_acceptance.py`

```bash
make db-up          # 如未启动
.venv/bin/python -m pytest services/api/tests/test_proactive_acceptance.py -v
```

## 场景映射

| 编号 | 验证目标 | 测试用例 | 关键断言 |
|---|---|---|---|
| AT-P01 | 出发前签证时效触发 | `test_at_p01_scheduled_visa_trigger_proposes_with_evidence` | 48h 后出行的签证建议自动生成；`status=PENDING`、`requires_approval=true`、含 `TRIP_WINDOW` / `VISA_RULE_CATALOG` / `VISA_RULE` 证据、`rationale` 非空 |
| AT-P02 | 重复触发抑制 | `test_at_p02_same_dedupe_key_never_proposes_twice` | 同一 `dedupe_key` 连续扫描两次只产生 1 条建议、1 条触发；第二次 `deduped≥1` |
| AT-P02 | 每日打扰上限 | `test_at_p02_daily_cap_stores_but_does_not_push` | `cap=3` 时第 4 条建议 `push_status=WITHHELD`、`push_reason=DAILY_CAP`、`notified_at IS NULL`，且不出现在用户列表 |
| AT-P03 | 人审 → 受控执行 → 审计 | `test_at_p03_approval_executes_and_writes_audit_chain` | 批准前无审批记录、无执行结果；批准后 `approvals` 1 条、`status=EXECUTED`、执行结果 `PREPARED` 且明确「未对外」；审计链 `DETECTED→EVALUATED→PROPOSED→APPROVED→EXECUTED→AUDITED`；同幂等键重放字节一致且不重复审批 |
| AT-P04 | 驳回 → 误报回收 | `test_at_p04_dismiss_marks_false_positive_and_lowers_confidence` | 驳回后 `status=DISMISSED`、原因落库、`memory_feedback.weight_delta<0`；同类新建议置信度下降、`feedback_penalty>0` |
| STATE | 状态型触发 | `test_state_trigger_detects_missing_coordinates` | 无坐标地点产生 `PLACE_COORDINATES_MISSING`；触发记录 `type=STATE`、`rule_id=missing_coordinates` |

## 边界与已知限制

- **外部源能力不是同一成熟度**：天气与航班已有公开源接入边界；航班源 OpenSky 提供的是按
  callsign 查询的航空器状态快照，不是用户的预订记录、时刻表或航司延误事实。签证政策与汇率
  仍依赖可信信号输入；`visa_check` 只使用仓库内离线目录 `app/data/visa_catalog.json`，
  所有文案都写「需人工核验」，不宣布签证结论。
- **推送语义**：`PUSHED` 表示「进入用户可见的建议流」，当前没有 APNs / 邮件通道；
  `WITHHELD` 表示入库但不展示。两者分离是为了让「为什么没看到」可解释。
- **执行只准备不对外**：`approve` 后的执行结果全部是草稿 / 清单 / 待办，写入
  `proactive_suggestions.execution_result`；不发送、不预订、不提交签证。
- **测试卫生**：用例创建的出行在结束时删除，避免污染每日打扰上限；`approvals` 是通用表，
  清理时先按 `subject_id` 删除。
## H2.16 — 天气信号的计划影响匹配

| 验收点 | 测试 | 关键断言 |
|---|---|---|
| 精确地点 + 时间窗 | `test_weather_signal_maps_exact_place_and_time_window_to_active_plan` | 仅同一 `place_id` 且停靠时段与信号有效期重叠的 PlanStop 被标记；同地点但时间不重叠、同时段但地点不同的拜访均不被误报 |
| 人审边界 | 同上 | 批准只生成 PREPARED 影响摘要；Trip version 和 active plan 指针不变 |

天气计划影响评估不使用 LLM，不按名称/邻近坐标猜测地点。缺少 ACTIVE 计划、place_id 或有界有效期时会返回显式评估缺口，不声称已识别具体受影响拜访。


## H2.17 — 天气影响扩展到交通段上下文与航班显式关联边界

| 验收点 | 测试 | 关键断言 |
|---|---|---|
| 天气影响关联交通段 | \`test_weather_signal_maps_exact_place_and_time_window_to_active_plan\` | 只列出端点直接连接到时空匹配拜访的 PlanTravelLeg；有前后停靠时计算估算时刻间隔与余量，缺少起点时间时明确返回未知 |
| 证据可追溯 | 同上 | 建议证据包含 PLAN_TRAVEL_LEG，引用交通模式、方向关系、估算余量与 stale 状态 |
| 航班关联安全边界 | \`test_flight_signal_does_not_auto_match_air_leg_without_booking_link\` | OpenSky callsign/icao24 不自动绑定 AIR 段；未关联的 AIR 段列为人工核验候选，linked_air_leg_count 保持 0 |

交通段余量仅按 \`next_stop.arrive_at - previous_stop.depart_at - travel_minutes\` 计算，是计划时刻与当前交通估算之间的派生余量；不代表实时延误、道路封闭或实际可用缓冲。任何外部天气关联都不会自动增加延误值或改变后续拜访时刻。

当前没有航班预订/出票/机场时刻的权威数据实体，故 flight telemetry 与 AIR leg 必须经后续显式预订关联流程确认。调用航班源返回的 callsign 或 icao24 本身不足以证明其对应某位旅客的某张机票。
