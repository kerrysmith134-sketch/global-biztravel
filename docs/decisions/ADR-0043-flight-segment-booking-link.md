# ADR-0043：机票行程段与规划交通段分离，并使用显式人工绑定

- 状态：已接受（Accepted）
- 日期：2026-11-26
- 相关模型：TripFlightSegment、FlightSegmentPlanBinding、PlanTravelLeg、PlanVersion
- 相关迁移：0049_flight_segment_bookings

## 背景

规划引擎中的 PlanTravelLeg 是某一份不可变计划快照中的交通估算段，可能只有预计耗时、距离和来源；它不是订票记录，也不一定有航班号、机场或承运人信息。把预订字段直接写入计划快照会破坏计划不可变语义。反过来，仅按航班号或 OpenSky callsign 自动关联，也无法证明航空器 telemetry 对应旅客购买的具体航段。

## 决策

1. TripFlightSegment 独立保存机票行程段：航班 designator、出发/到达 IATA 机场、带时区偏移的计划起降时间、机场 IANA 时区、订票状态、录入来源及可选的供应商 tracking reference。
2. 不存储 PNR、票号、护照号或完整订座凭证。预订凭证的加密导入与保管属于另一安全域，不应混入普通行程模型。
3. FlightSegmentPlanBinding 独立记录用户将一段机票行程绑定到某一 PlanVersion 的某个 PlanTravelLeg 的决定。只有 ACTIVE 计划里的 mode=AIR leg 能新建绑定。
4. 绑定变更采用 RELEASED 历史记录 + 新 ACTIVE 记录，不覆盖旧关联。计划切换后，旧绑定保留为 STALE_PLAN_BINDING，用户需要显式重新绑定。
5. 通过复合外键强制绑定的组织、出行、计划版本与交通段归属一致；RLS 仍以 organization_id 为边界。

## 后果

- 机票行程数据不污染不可变规划快照。
- 绑定与重绑可审计，并可明确展示它对应哪一版计划。
- 一个 AIR 规划段可以对应多段真实航班（例如中转）；单个机票航段一次只绑定一个规划段。
- 本 ADR 只建立机票段录入与显式绑定基础。供应商状态采集、航班延误判定、自动备选改签和连锁行程传播必须在后续单独验证，不由本模型隐式推断。
