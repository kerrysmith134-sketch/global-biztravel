# ADR-0019：汇率由组织显式录入，缺汇率不折算

- 状态：已接受（Accepted）
- 日期：2026-10-28
- 相关文件：迁移 `0024_fx_rates`、`app/models/fx.py`、`app/services/fx_service.py`、`app/api/routes/fx.py`、预算折算 `expense_service.budget_summary`
- 相关需求：PRD 方向六「费用与财务闭环」、设计文档第 14 章「汇率：公开 API，降级用最近缓存」

## 背景

多币种费用需要给管理者一个合并口径，但本地没有真实汇率供应商。若系统内置一个"示例汇率"
或把未知币种按 1:1 合并，预算结论会静默失真。设计文档要求外部源不可用要"降级但可用，并
标记数据新鲜度，不得伪造数据"。

## 决策

1. 新增组织级 `fx_rates`：`base_currency / quote_currency / rate / as_of / source`；语义固定为
   **1 单位 base = rate 单位 quote**。同一 `(组织, base, quote, as_of)` 唯一，重复录入覆盖。
2. 只有 ADMIN 能录入；所有成员可查询"每个币种对的最新一条"。
3. 预算接口新增可选 `base_currency`。折算只使用组织已录入且已生效（`as_of` 不晚于当前时间）的最新汇率；支持反向记录（取倒数
   或直接用反向因子），并在响应 `via_inverse` 标注来源方向。
4. 任一实际费用币种或计划费用币种缺汇率时：`conversion_complete=false`、`converted_*`
   返回 `null`、`unconverted_currencies` 列出缺口，**不给不完整的总计**。
5. 计划费用存在未知分段（`cost_unknown_count>0`）时同样视为折算不完整。
6. 默认行为不变：不传 `base_currency` 时仍按币种分列，只对同币种计算差额。

## 后果

- 好处：预算合并口径可审计（每条汇率有来源与生效时间）；没有外部供应商时功能仍然可用，
  且不会给出误导性总计。
- 代价：上线真实汇率源前需要管理员手工维护；汇率不是实时行情，响应中必须同时读
  `fx_conversions[].as_of` 判断新鲜度。
- 客户端附带修复：后端 `Decimal` 序列化为 JSON 字符串，Swift 默认只接受数字，导致费用/预算
  模型此前可能解码失败；新增 `decodeDecimal` 同时接受字符串与数字，并补齐相关单测。
