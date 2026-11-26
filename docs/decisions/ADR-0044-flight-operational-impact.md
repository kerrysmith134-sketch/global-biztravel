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
