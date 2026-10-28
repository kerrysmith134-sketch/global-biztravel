# ADR-0017：报销单是费用记录的不可变快照

- 状态：已接受（Accepted）
- 日期：2026-10-28
- 相关文件：迁移 `0022_expense_reports`、`app/models/expense_reports.py`、`app/services/expense_report_service.py`、`app/api/routes/expense_reports.py`
- 相关需求：PRD 方向六「费用与财务闭环」

## 背景

费用记录是现场随手录入的原始数据，可能被修正；报销单一旦提交给审批人，内容就不能再被源数据
悄悄改变。如果把报销单做成"实时聚合费用记录"，删一条费用就会改变已提交金额，审计与审批结论
都失去依据。

## 决策

1. 创建报销单时把费用明细**快照**到 `expense_report_items`（含类别/金额/币种/日期/备注/凭证引用）。
2. `expense_id` 只是溯源引用（源费用删除后 SET NULL），快照金额不随之变化。
3. 状态机：`DRAFT → SUBMITTED → APPROVED/REJECTED`；只有 DRAFT 可提交，只有 SUBMITTED 可审批。
4. 提交写 `workflow_instances`（EXPENSE_REPORT），审批写 `approvals`（subject_type=expense_report）并结算 workflow。
5. 多币种按币种分组返回，不做汇率换算；审批人看到的就是快照的币种明细。

## 后果

- 好处：已提交内容不可被源数据篡改；审批链与工作流可回放。
- 代价：报销单创建后新增的费用不会自动进入该单；需要重新建单或走后续"补录"流程。
