# ADR-0044：航班运营事件独立建模，并按计划缓冲传播影响

- 状态：已接受（Accepted）
- 日期：2026-11-27
- 相关模型：FlightOperationalUpdate、TripFlightSegment、FlightSegmentPlanBinding、PlanTravelLeg、PlanStop
- 相关迁移：0050_flight_operational_updates

## 背景

OpenSky 等 ADS-B 航迹源提供的是航空器遥测快照，不能证明航司的计划变更、延误、取消或备降。将 on_ground、位置、地速或最后联系时间解释成 ETA/取消/延误，会把不具备该语义的数据升级成运营事实。

## 决策

1. FlightOperationalUpdate 与 ExternalSignal(FLIGHT_STATUS) 分离：前者保存规范化运营事件，后者是主动式运行时消费的信号封装。
2. 每条运营事件必须明确引用 trip_id + flight_segment_id，并带 Provider、Provider event ID、航班号、原始起讫机场、事件类型、观察时间、置信度与内容摘要哈希。只允许经外部集成适配器规范化后的事件进入，不直接将 OpenSky telemetry 当成运营更新。
3. (organization_id, provider, provider_event_id) 唯一。相同事件 ID、相同内容可幂等重放；相同事件 ID、不同内容返回冲突，历史事件不被覆盖。
4. flight_operational_updates 只允许插入，不允许更新。事件原文不保存在该表；可选的源 payload SHA-256 仅用于外部内容留痕，避免把第三方原始请求或敏感预订数据混入业务域。
5. 运营更新必须再次通过当前 ACTIVE PlanVersion 中的显式 FlightSegmentPlanBinding，并验证绑定目标为 AIR leg。没有当前绑定时只报告未绑定/过期状态，不猜测关联。
6. 对有预计到达时间的延误事件，先计算航班到达偏移，再扣除“计划中的航班落地到首个拜访停靠点”时间余量；后续停靠按 PlanStop 时刻表传播，使用停靠内服务余量及相邻 PlanTravelLeg 的计划间隙余量逐段吸收延误。
7. 只有存在完整、非 stale 的相邻交通段时，才继续计算下游预约窗口。缺少交通段、交通段 stale、计划时序自相矛盾时，后续拜访标记为未评估，不假设缓冲为零。
8. 取消/备降会标记航段之后的拜访为受扰动/时间未定，不伪造 ETA；改签、地面交通重排和计划换版都必须走后续独立方案与审批流程。

## 后果

- OpenSky 轨迹与航司/机场运营事实保持不同数据语义。
- 运营事件可审计、可幂等重放，内容冲突不会静默覆盖。
- 延误影响可以解释到首个拜访的落地余量、相邻交通段余量与具体预约窗口。
- 本轮只做事实接入和确定性影响评估，不自动改票、不自动改写 PlanVersion，也不声称已连接某个航空数据 Provider。


## H2.21.2 补充：不可变事件与可变信号封装之间的完整性校验

- `ExternalSignal` 的 `operational_update_id`、`operational_update_content_sha256` 与 `operational_update_type` 是服务端派生字段，客户端不得自行提交。必须同时保留规范化 `flight_operational_update` 事件内容。
- 影响评估时重新核对服务端事件引用、内容 SHA-256、事件类型、Provider、dedupe key、观察时间、`valid_from`、`valid_until`、置信度与摘要。任一字段不一致时停止传播，返回 `OPERATIONAL_UPDATE_EVIDENCE_MISMATCH` 或 `OPERATIONAL_UPDATE_ENVELOPE_MISMATCH`。
- 该校验防止把已存在的旧事件 ID 重新包装成一个新的观察时间/有效期，从而人为延长预警有效性；同时保留同一事件相同内容的幂等重放。
- 新增 PostgreSQL 集成断言：服务端引用字段伪造被请求层拒绝；信号内容指纹被篡改时影响评估被拒绝；观察时间被篡改时有效封装校验被拒绝。以上测试必须以严格 CI 的真实 PostgreSQL 运行结果作为最终验收依据。


- **嵌套事件逐字段核对**：影响评估重新使用 `FlightOperationalUpdatePayload` 校验 `ExternalSignal.payload.flight_operational_update`，并将航段 ID、Provider 事件 ID、类型、航班号、计划机场、预计起降时刻、备降机场和源 payload 哈希逐项与 append-only 事件行比较。不能仅因嵌套对象存在或外层摘要匹配就接受。
