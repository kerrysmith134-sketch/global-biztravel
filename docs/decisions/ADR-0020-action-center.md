# ADR-0020：行动中心是已有事实的聚合视图，不是第二套待办表

- 状态：已接受（Accepted）
- 日期：2026-10-28
- 相关文件：`app/services/action_center_service.py`、`app/api/routes/action_center.py`、iOS `ActionCenterView`
- 相关需求：PRD S8-08「主动通知与待办中心」、页面 06「Action Center」

## 背景

建议、报销单、后台任务和 CRM 同步分别有自己的状态机。如果为行动中心再建一张"待办表"，
就必须处理双向同步：待办完成后如何回写业务表、业务表状态变化如何更新待办、两边不一致时谁赢。
这条路径会制造第二份真相，最终让用户看到互相矛盾的"已完成/未完成"。

## 决策

1. 行动中心**不建表**，`GET /v1/action-center` 每次从权威表实时聚合：
   - `proactive_suggestions` → 待决定 / 最近已完成；
   - `expense_reports(SUBMITTED)` → 待审批；
   - `background_jobs(PENDING/RUNNING/FAILED/DEAD)` → 执行中 / 失败；
   - `crm_sync_jobs(PREPARED/FAILED)` → 待外部确认 / 失败。
2. 统一输出 `category / kind / entity_id / trip_id` 与 `available_actions`；客户端据此展示，
   不解析业务文案。分类顺序固定为：待决定 → 待审批 → 失败 → 执行中 → 待确认 → 已完成。
3. `crm_sync` 类型的后台任务不重复计入"执行中/失败"，由 CRM 同步记录统一承载，避免同一件事
   出现两条卡片。
4. 已完成区只保留最近 7 天、最多 5 条建议，防止行动中心退化成历史日志。
5. 所有查询按 `organization_id` 过滤（并受 RLS 兜底）；可选 `trip_id` 只看一次出行。
6. 客户端只对建议提供就地批准/驳回；报销单与任务通过"查看行程"跳转，审批仍在业务页面执行。

## 后果

- 好处：状态只有一份真相；任何业务表状态变化立即反映，不需要补偿任务；新增事项类型只需
  在聚合器里加一个数据源。
- 代价：每次打开行动中心要扫四张表；当前数据量下可接受，后续可按 `(organization_id,status)`
  索引与分页扩展。聚合结果不含"用户已读"语义——是否打扰用户仍由通知策略与建议推送状态决定。
