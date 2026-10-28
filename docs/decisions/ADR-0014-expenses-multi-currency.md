# ADR-0014：费用按币种分列，不做假汇率换算

- 状态：已接受（Accepted）
- 日期：2026-10-28
- 相关文件：迁移 `0019_trip_expenses`、`app/models/expenses.py`、`app/services/expense_service.py`、`app/api/routes/expenses.py`
- 相关需求：PRD 方向六「AI 差旅费用与财务闭环」

## 背景

PRD 要求按出行归集费用并与预算对比。多国出差天然多币种；MVP 没有真实汇率源，如果为了
"好看"把 USD/CNY 直接相加或按 1:1 换算，会制造错误的预算结论。

## 决策

1. `trip_expenses` 记录类别、金额、币种、发生日期、备注与凭证引用（不存票据二进制）。
2. 预算汇总按币种分组返回；`actual_total_in_budget_currency` 只在存在同币种记录时才有值。
3. `variance_vs_budget` 只在同币种下计算；`variance_vs_plan` 只在计划费用币种与预算币种一致时计算。
4. 计划费用未知段（`cost_unknown_count > 0`）显式返回；不把未知费用当 0 参与差额。
5. 汇率换算、报销单、凭证 OCR 与合规规则留到接入真实汇率/财务系统后实现。

## 后果

- 好处：预算结论可解释；不会因假汇率误导用户。
- 代价：多币种用户暂时需要分别看各币种合计；真正的合并报表需要真实汇率与时间点。
