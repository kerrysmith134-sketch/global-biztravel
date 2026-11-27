# ADR-0046：预约恢复动作复用 VisitFollowUp 人工决策与执行生命周期

- 状态：已接受（Accepted）
- 日期：2026-11-27
- 相关能力：ADR-0045 Appointment Recovery Agent、ADR-0020 Action Center、ADR-0011 Plan Change Workflow、H2.12 Visit Follow-up Lifecycle

## 背景

H2.22 已经把可信航班影响评估转换为确定性恢复动作，并将完整输出保存在 APPOINTMENT_RECOVERY AgentRun 中。但动作只是摘要中的建议，尚未进入逐项人工决策和实际跟进生命周期。

项目已经有 VisitFollowUp：它具备来源指针、接受/驳回、锁行状态转换、不可变事件历史及 Action Center 聚合。再次建设 RecoveryTask / RecoveryApproval 会形成第二个任务中心和第二套状态机。

## 决策

1. 新增 VisitFollowUp 来源类型 APPOINTMENT_RECOVERY。每项动作通过 source_agent_run_id + source_index 精确定位，不复制或重写 AgentRun 中的原始动作。
2. 只有 scope=VISIT 且 action.visit_id 与请求路径 Visit 完全一致的动作可转为拜访跟进；TRIP 级动作留在恢复简报中，必须走出行级复核，不绑定到任意 Visit。
3. 服务端验收来源时逐项验证 AgentRun、WorkflowInstance 和来源 ProactiveSuggestion 的三方引用、组织/Trip 归属、成功状态、action index 和 Visit ID。
4. 用户 ACCEPT 后，动作成为 OPEN VisitFollowUp，进入既有 Action Center；用户 DISMISS 后保留 DISMISSED 状态与事件审计。
5. 同一个 AgentRun 以行锁序列化人工决策；既有唯一 source_key 保证单一动作只处理一次，HTTP 写入继续沿用 Idempotency-Key。
6. 完成某一恢复跟进项仅代表人工工作项完成，不代表客户预约已经确认或正式计划已变更。
7. 恢复动作不直接更改正式 Visit/Trip/PlanVersion/PlanStop，不订票、不改签、不联系客户。客户确认时段后，仍须走原有 Plan Change → 求解 → 验证 → diff → 人工激活链。

## 后果

- 不新增任务中心或审批模型；Action Center 仍只有一个来源汇总面。
- 恢复动作可以逐项接受/驳回，接受后由原有跟进状态机追踪到完成。
- 原始航班事件、影响评估、Workflow、AgentRun 保持可追溯关系。
- 新增迁移只扩展 visit_follow_ups 的来源类型与来源字段组合 CHECK。
