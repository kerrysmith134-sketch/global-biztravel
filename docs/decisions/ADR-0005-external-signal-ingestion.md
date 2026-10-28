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
