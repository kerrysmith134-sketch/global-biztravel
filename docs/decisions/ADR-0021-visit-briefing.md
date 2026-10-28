# ADR-0021：拜访简报是只读聚合，缺口必须显式列出

- 状态：已接受（Accepted）
- 日期：2026-10-28
- 相关文件：迁移 `0025_visit_objective`、`app/services/visit_briefing_service.py`、`app/api/routes/visits.py`、iOS `VisitBriefingView`
- 相关需求：PRD 页面 07「Visit Workspace」8.1 拜访前

## 背景

拜访前需要把客户档案、地点、历史拜访、跟进项与复盘记忆放在一屏。CRM 实时商机与资料附件
尚未接入；如果简报对缺失内容留空或编造"暂无商机"，用户会把"没显示"误读为"没有问题"。

## 决策

1. `GET /v1/visits/{id}/briefing` 只读聚合已有表：`customers`、`places`、`visits`、
   `visit_records`、`trip_memory_summaries`；不新建简报表，避免第二份真相。
2. 本次拜访目标落在 `visits.objective`（迁移 `0025`），可由 `PATCH /v1/visits/{id}` 修改；
   空值表示"尚未设定"，UI 明确提示，而不是显示空白。
3. 历史拜访只取**同一客户、且结束时间早于本次出行开始**的记录，最多 10 条；跟进项来自这些
   记录的 `follow_up_tasks`，最多 20 条。跟进项没有完成状态模型，因此只作为历史线索展示，
   不标"待办/已完成"。
4. 复盘记忆按 `customer_id` 取当前有效（`valid_until IS NULL`）的 `TRIP_REVIEW` /
   `CUSTOMER_NOTE`，最多 5 条。
5. 未建模信息通过 `data_gaps[]` 返回机器码与说明：`CRM_OPPORTUNITY_NOT_CONNECTED`、
   `VISIT_MATERIALS_NOT_MODELED`；拜访未关联客户时额外返回 `CUSTOMER_NOT_LINKED`。
6. 客户联系人字段沿用既有 PII 加密存储与授权读路径；简报不新增公开字段。

## 后果

- 好处：一屏完成访前准备；缺口可见，不会把"未接入"误判为"无风险"；历史与记忆直接复用
  已有权威数据，不需要额外录入。
- 代价：CRM 商机数与附件仍需在外部系统查看；跟进项没有闭环状态，后续若引入任务表应把
  简报改为引用任务 ID，而不是继续从记录 JSON 里扁平化。
