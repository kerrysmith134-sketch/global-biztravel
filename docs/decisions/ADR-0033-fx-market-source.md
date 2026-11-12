# ADR-0033 FX 行情源写入同一 fx_rates 表，行情行以 source 标记

- 状态：已接受（2026-11-12）
- 落点：`app/integrations/fx.py`、`app/services/fx_market_service.py`、
  `POST /v1/fx-rates:sync`（ADMIN）、Worker `fx_rate_sync` 任务、配置 `GBT_FX_PROVIDER`
- 相关：ADR-0019（手工录入与缺口语义）、ADR-0018（适配器"不伪造成功"模式）、
  ADR-0005（可信信号接入）
- 关闭：implementation-status §7-8「汇率行情源」

## 背景

ADR-0019 落地了组织显式汇率：`fx_rates` 表 + 缺汇率不折算，但汇率只能管理员手工维护
（§7-8）。台账预留的方向是"公开 API 接入后应写入同一 `fx_rates` 表并保留
`source`/`as_of`，折算与缺口逻辑不变"。

行情源选型约束：免钥（本地与 CI 不持密钥）、覆盖商旅币种（CNY/USD/SGD/JPY/EUR…）、
确定性可追溯。**Frankfurter**（ECB 欧央行参考汇率公开镜像）满足全部三条：工作日更新、
30+ 币种、无需注册。

## 决策

1. **同一张表，不建第二套汇率存储**。行情同步 = 替管理员往 `fx_rates` 写行；
   `source="fx-frankfurter"`、`as_of=行情日期（UTC 零点）`、
   `created_by_user_id=NULL`（行情行不归到任何管理员名下）。
   `(org, base, quote, as_of)` 唯一约束天然幂等：同一行情日重复同步是覆盖写。
2. **折算与缺口逻辑零改动**（ADR-0019 第 3/4 条原文适用）：预算折算照旧取
   "最新已生效一条"；手工录入的更 `as_of` 的行天然覆盖行情行——**管理员录入
   永远可覆盖行情**（组织口径优先于市场口径）。
3. **显式触发，不做自动定时**。`POST /v1/fx-rates:sync`（ADMIN）按
   `base_currency + symbols` 入队 `fx_rate_sync` 任务（幂等键 + dedupe 复用在途）。
   不做 Worker 周期拉取：ECB 工作日更新、差旅预算对实时性不敏感，
   自动拉取会引入"无人审的外部数据变更"，违背 I3 的精神。
4. **缺口与失败诚实**（沿用 ADR-0018/0005 模式）：行情没覆盖的币种进任务结果
   `missing` 列表，不伪造；`GBT_FX_PROVIDER=unavailable`（默认）时任务终态
   FAILED 并注明未配置；网络/解析失败按不可重试失败结算（行情是日期快照，
   重试只会在下个行情日拿到新数据）。
5. **适配器边界与 CRM 同构**：`FxMarketProvider` Protocol + `get_fx_provider()`
   按配置解析；新供应商（如快照级 Open Exchange Rates）实现同一协议即可接入。

## 后果

- **+** §7-8 本地闭环：汇率数据可全自动获得（配置一个环境变量），预算合并口径
  不再依赖手工维护；每条汇率仍可审计来源与生效时间。
- **+** 折算语义、iOS 预算页、缺口提示完全不变（契约只增不改）。
- **−** ECB 参考汇率是日频中间价，不含点差；对差旅预算足够，对结算级精度不够
  （与 ADR-0019 "不做假精度"的立场一致）。
- **−** 公开源单点：Frankfurter 不可用时同步失败可见，管理员仍可手工录入兜底
  ——降级路径就是 ADR-0019 的原路径。
