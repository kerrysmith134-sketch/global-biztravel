# ADR-0005：EXTERNAL 触发源采用「可信信号接入」，不在本地伪造外部数据

- 状态：已接受（Accepted）
- 日期：2026-10-28
- 相关文件：迁移 `0011_external_signals`、`app/models/integrations.py`、`app/services/external_service.py`、`app/api/routes/integrations.py`、`app/services/proactive_service.py`
- 相关需求：M1（主动式运行时 EXTERNAL 分类）、S1（外部集成边界）

## 背景

设计文档第 8.1 章把 `EXTERNAL`（签证政策/汇率/天气/航班）列为四类触发源之一，第 14 章要求
外部源不可用时"降级但可用，不得伪造数据"。本地开发没有真实供应商凭证，如果为了演示而在代码里
写死"德国签证周期变了/柏林明天暴雨"，主动建议就会变成虚构事实——这违反不变量 I1/I3。

## 决策

1. **系统只消费"已上报的信号"**：新增 `external_signals` 表与 `POST /v1/integrations/signals`
   （ADMIN）。信号包含 provider、类型、对象（国家/出行/客户）、payload、置信度、观察时间与有效期。
2. **主动式规则只做匹配与建议**：`_external_candidates` 读取与出行相关且未过期的信号，生成
   `VISA_POLICY_UPDATE` / `WEATHER_DISRUPTION_ALERT` / `FX_RATE_ALERT` / `FLIGHT_STATUS_ALERT`
   建议；措辞固定为"外部源报告…需按官方渠道复核"。
3. **没有信号就不产生建议**：本地不内置任何外部数据；验收通过测试注入真实结构的信号。
4. **幂等接入**：`(organization_id, provider, dedupe_key)` 唯一；同键重推更新原行。
5. **信号变化与建议去重分离**：payload 变化会生成新的建议去重键，但同类型建议仍受 24h 冷却
   与每日上限约束，不会因供应商频繁推送而打扰用户。

## 后果

- 好处：不伪造数据；未来接真实供应商时只需把回调/轮询结果转成信号，不动主动式逻辑。
- 代价：本地演示需要手动注入信号；真实供应商接入、鉴权、字段映射仍需在集成层完成。


## H2.16 补充：天气信号按精确地点与计划时间窗评估

- 对 `WEATHER_DISRUPTION` 信号，仅使用规范 `place_id` 匹配当前 ACTIVE PlanVersion 的 PlanStop；不使用城市名称相似或附近坐标推断。
- 潜在影响必须同时满足停靠点的 `[arrive_at, depart_at)` 与信号的有界 `[valid_from, valid_until)` 时间窗口重叠。没有有效结束时间时，不把当前天气外推到未来行程。
- 评估结果随主动建议保存，并引用原始 ExternalSignal、ACTIVE PlanVersion 与 PlanStop。评估只表达时空潜在暴露，不宣布行程不可行。
- 批准建议仍只生成 PREPARED 影响摘要，不修改 Trip 输入、不创建计划变更、不自动改签。正式计划调整仍通过 ADR-0011 的唯一计划变更入口，并由用户确认新版本。


## H2.17 补充：天气交通上下文与航班关联约束

- 天气评估除精确匹配的受影响拜访外，还会列出端点直接连接这些拜访的 \`PlanTravelLeg\`。仅在两个端点均位于同一 ACTIVE 计划时计算估算时刻余量：\`to_stop.arrive_at - from_stop.depart_at - travel_minutes\`；首段缺少起点停靠时，余量保持未知。
- 相关交通段只是人工复核上下文，不代表交通本身已受天气影响。估算余量不是实时延误，也不允许系统据此传播延误或自动重排。
- OpenSky 航班数据是航空器 telemetry。现有 \`PlanTravelLeg\` 不包含航班号、订座引用或机场计划时刻，因此即使 callsign/icao24 存在，系统也只展示未关联的 AIR 段，不自动匹配或宣称航班延误。建立 booking-to-leg 关联前必须引入权威航班/预订标识和用户确认流程。
