# ADR-0047：预约恢复重评必须使用更新航班证据与明确人审的客户窗口

- 状态：已接受（Accepted）
- 日期：2026-11-27
- 相关能力：ADR-0044 航班运营事件影响传播、ADR-0045 Appointment Recovery Agent、ADR-0046 VisitFollowUp 人工决策生命周期

## 背景

H2.23 使预约恢复动作可以被逐项接受并进入 Action Center，但执行中的事项缺少一条闭环：补充证据、重算 ETA 与预约关系、保留前后评估，并决定是否需要生成新的计划变更提案。

不能把用户输入的 ETA、客户邮件文字或模型推断当成航班事实，也不能为了让恢复流程“自动完成”而原地覆盖 Visit 或 ACTIVE PlanVersion。

## 决策

1. 重评必须从原 FollowUp 的 source AgentRun 开始，重新核对 AgentRun / Workflow / ProactiveSuggestion、原 FlightOperationalUpdate ID 和 content SHA-256。
2. 必须发现同一原始航班段上晚于原事件、尚未过期的最新 FlightOperationalUpdate；对替代航班动作，调用者必须显式引用已录入且已绑定当前 ACTIVE PlanVersion 的不同机票段。机票段仍须满足 BOOKED/TICKETED，新的运营事件须由现有 external_impact_service 完整验证。
3. 影响传播只通过现有确定性 external_impact_service 针对当前 ACTIVE PlanVersion 计算。计划缺失、计划版本不匹配、没有 ETA、交通段缺失/过期/时序不一致或传播未完成，均 fail-closed，不给出 CHANGE_VISIT_TIME 提案。
4. 客户预约窗口必须由出差人显式提交起止时间和 IANA 时区，并记录确认方式及可选邮件/CRM 活动参考编号。系统不保存客户通信正文或联系方式；该证据只标记 PARTIALLY_VERIFIED，明确不是外部独立验证。
5. 对同一 FollowUp 和相同规范化输入 SHA-256 只生成一次重评 AgentRun；事件、Evidence、WorkflowInstance 和 AgentRun 在同一数据库事务落库。
6. Evidence 分别记录不可变航班事件（VERIFIED）、当前计划影响快照、客户窗口人审陈述（PARTIALLY_VERIFIED）和重评结果；AgentRun 输出保留 before/after、差异、data gaps 及输入摘要。
7. 仅当当前计划传播完整且预计到达符合窗口时，可以返回 CHANGE_VISIT_TIME 建议及 base_plan_version_id / base_trip_version。此端点不调用 Plan Change API、不修改 Visit/Trip/PlanVersion/PlanStop/FlightSegment、不改变 FollowUp 状态。
8. 新增 REASSESSED 只追加事件，不代表客户预约已更改，也不代表跟进事项完成；真正的计划变更必须由用户另行提交现有 Plan Change 工作流。

## 后果

- H2.24 扩展既有 FollowUp / Workflow / AgentRun / Evidence，未新增平行任务表或审批系统。
- 重新评估输入具备来源、时效、内容摘要和租户边界；缺失证据可解释且不会被当作“无风险”。
- 用户可从 Visit workspace 看到最近一次重评与需人工提交的计划变更提案。
