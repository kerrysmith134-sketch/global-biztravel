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


## H2.19 — 航班跟踪仅关联到已确认的机票段

| 验收点 | 测试 | 关键断言 |
|---|---|---|
| 显式航班跟踪 | \`test_open_sky_tracking_requires_explicit_segment_provider_reference_and_current_binding\` | pull-flight 将显式 flight_segment_id 写入信号；provider/reference 必须与机票段配置匹配，且当前 ACTIVE plan binding 仍指向 AIR leg，才返回 TRACKING_MATCHED_TO_CURRENT_AIR_LEG |
| 不从 callsign 猜预订 | 同上 | 即使无 flight_segment_id 的 signals.callsign 与机票 tracking_reference 相同，也维持 AIR_LEGS_REQUIRE_EXPLICIT_LINK，linked_air_leg_count=0 |
| Provider / reference 不匹配 | \`test_open_sky_tracking_rejects_provider_and_reference_mismatch\` | 两种不匹配均在请求外部数据前返回 Unprocessable，不写入 FLIGHT_STATUS |
| 跨出行信号隔离 | \`test_pull_flight_ingests_signal\` | 相同 provider/callsign/icao24 被两个 Trip 跟踪时，两个 dedupe_key 不同，两个 subject_ref 均保留 |
| 延误判断边界 | 同上 | 评估标记 NOT_DERIVABLE_FROM_AIRCRAFT_TELEMETRY，不推断延误、取消或 ETA |

只有机票段 ID、tracking provider/reference、当前 ACTIVE 计划绑定三者同时通过核验，才可将 telemetry 与某一 AIR leg 关联。OpenSky 的位置、地速和高度是航空器快照，不是航司运行状态；正式延误判定仍需要航司/机场运行数据源。


## H2.20 — Flight proactive audit evidence and binding-aware dedupe

| 验收点 | 测试 | 关键断言 |
|---|---|---|
| 已绑定航段证据链 | \`test_linked_flight_suggestion_tracks_booking_binding_as_evidence_and_dedupe_input\` | 建议证据同时包含 Flight Segment、FlightSegmentPlanBinding、当前 PlanTravelLeg 和 PlanVersion |
| 同一计划重绑 | 同上 | 同一 FlightSegment 在同一 PlanVersion 内解绑再绑，即使 signal payload 未变，binding_id 改变也会产生新的 dedupe key |
| 冷却期与审计 | 同上 | 新绑定生成新的建议审计记录，但 24 小时提醒冷却仍生效，新记录为 SUPPRESSED，影响评估引用新的 binding id |

航班外部信号的摘要不再只依赖供应商 payload 和 PlanVersion；显式机票段、当前 binding 以及关联 AIR leg 都参与摘要。这样同一计划内人工更正航段绑定时，系统会留下新的可追溯评估，而不是因旧 dedupe key 把新绑定静默吞掉。


## H2.21 — Provider 航班运营事件与预约影响传播

| 验收点 | 测试 | 关键断言 |
|---|---|---|
| 运营事件与遥测分离 | test_provider_operational_update_propagates_delay_through_plan_buffers_and_windows | FLIGHT_STATUS 信号携带显式机票段与规范化运营事件；OpenSky telemetry 不被改写为延误事实 |
| 事件幂等与冲突 | 同上 | 相同 provider/provider_event_id 与相同内容只保留一条；同一事件 ID 的不同内容返回冲突，不覆盖旧事件 |
| 计划身份验证 | 同上 | 航班号与原始起讫机场必须匹配机票段；跨组织/出行或错误机票段不能进入影响计算 |
| 落地与地面余量 | 同上 | 航班到达偏移先扣除落地后至首个拜访的计划余量；后续延误再按每段交通时间与计划间隙余量传播 |
| 预约窗口 | 同上 | 每个停靠点返回 projected arrival、delay、预约窗口状态；缺失/stale 交通段后的停靠明确标记未评估 |
| 安全边界 | 同上 | 评估只产生建议和证据；不修改 Trip、PlanVersion、PlanStop，不发起改签或对外联系 |

运营更新采用 POST /v1/integrations/signals 的 FLIGHT_STATUS 信号封装：payload.flight_operational_update 必须包含规范化 Provider 事件与显式 flight_segment_id，dedupe_key 必须为 flight_ops:{provider_event_id}。本模型是 Provider-neutral 接入契约；具体航空数据商的鉴权、轮询/回调及字段映射由单独的 Provider adapter 完成。
