# ADR-0011：计划变更的唯一入口与 WorkflowInstance 持久化

- 状态：已接受（Accepted）
- 日期：2026-10-28
- 相关文件：`app/services/plan_change_request_service.py`、`app/api/routes/plans.py`、迁移 `0016_workflow_evidence_sync`
- 相关需求：PRD §6（统一计划变更 API）、AT-010

## 背景

此前地图、日历或 AI 想改计划，只能直接 PATCH 拜访，再手动触发重新生成；没有"变更请求"
这个可审计实体，也无法拒绝基于旧计划/旧 Trip 版本的修改。PRD 要求所有页面共用一个正式入口，
且 `ACCEPTED` 只表示"请求被接受处理"，不表示变更已生效。

## 决策

1. 新增 `POST /v1/trips/{trip_id}/plan-changes`，请求带
   `base_plan_version_id / base_trip_version / source / operation_type / payload`。
2. 校验基础版本：计划必须属于该出行且是当前生效计划；Trip 版本必须一致，否则 409
   `PLAN_VERSION_CONFLICT`。
3. 应用变更只改**规划输入**（时间窗、时长、取消、增删拜访），随后入队新的规划任务；
   不直接改激活计划，也不直接激活新版本。
4. 每次请求写一条 `workflow_instances`（`workflow_type=PLAN_CHANGE`，含 job_id、操作载荷、
   原因），作为可审计的变更请求实体；响应的 `change_request_id` 就是它的 id。
5. 首批支持 `CHANGE_VISIT_TIME / CHANGE_VISIT_DURATION / CANCEL_VISIT / ADD_VISIT / REMOVE_VISIT`。

## 后果

- 好处：变更可审计、可并发校验；地图/日历/AI 不再各自造一套写路径。
- 代价：一次变更必然重新求解；不涉及优化的备注类修改仍需单独设计快速路径。
