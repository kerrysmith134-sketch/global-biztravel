# ADR-0049：预约恢复计划变更必须绑定最新重评证据

## 状态

已接受

## 背景

预约恢复重评会基于最新航班运营事件、当前 ACTIVE PlanVersion 和出差人提供的客户预约窗口，生成一条需要人工提交的 `CHANGE_VISIT_TIME` 建议。若客户端只把建议转换成普通 Plan Change 请求，服务端仍可能收到被篡改的时间窗口、错误的 Visit ID，或基于已经过期的 Trip / Plan 版本提交的请求。客户端预检不能替代服务端的证据与并发校验。

## 决策

1. 预约恢复通过既有 `POST /v1/trips/{trip_id}/plan-changes` 提交，不建立平行的计划变更或审批系统。
2. 请求来源使用 `source=APPOINTMENT_RECOVERY`，并在操作参数中携带 `recovery_follow_up_id`。
3. 服务端必须重新读取该组织、Trip、Visit 下的 FollowUp，且只接受 OPEN / IN_PROGRESS 状态。
4. 服务端必须解析该 FollowUp 最新成功的 `APPOINTMENT_RECOVERY_REASSESSMENT` AgentRun，并验证其 Workflow 血缘、决策、数据缺口、人工提交标志、Visit、基础计划版本、Trip 版本及建议窗口。
5. 请求起止时间必须带 UTC offset，且换算成 UTC 后与 AgentRun 中的建议窗口完全一致。不得仅凭客户端提交的 `reason` 或 `source` 声明其来源。
6. 对同一 Trip 的计划变更先锁定 Trip 行；所有 Visit 创建、修改、删除采用一致的 Trip → Visit 锁顺序，避免不同幂等键并发提交时同时基于同一旧版本写入。
7. Plan Change 只更新规划输入并启动异步重求解。新计划仍需独立验证、Diff 审查，并由人明确激活；不得因预约恢复 Agent 的判断自动激活。

## 后果

- 不新增数据库表或迁移；复用 VisitFollowUp、AgentRun、WorkflowInstance、BackgroundJob 和 PlanVersion。
- 通用 MAP / CALENDAR 等计划变更路径保持原有行为；新增证据绑定只约束 `APPOINTMENT_RECOVERY` 来源。
- iOS 工作区负责展示建议、在线预检版本、提交、查询 Job、查看 Diff 和人工激活；最终授权与证据验证由服务端执行。
- 规划失败或取消后，可在未发生新的 Trip / ACTIVE Plan 版本变化时，对当前已提交的输入重新排队求解，不重复写入拜访时间窗。
