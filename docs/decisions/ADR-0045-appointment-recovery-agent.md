# ADR-0045：航班扰动后的预约恢复采用确定性 AgentRun 与显式人审

- 状态：已接受（Accepted）
- 日期：2026-11-27
- 相关模型：ProactiveSuggestion、WorkflowInstance、AgentRun、Evidence、PlanVersion、Visit
- 相关能力：ADR-0043 航段显式绑定、ADR-0044 航班运营影响传播

## 背景

航班运营事件经不可变事件行校验，并按当前 ACTIVE PlanVersion 的显式航段绑定传播后，系统已经能识别受影响或无法评估的客户拜访。但“识别风险”不等于“恢复预约”：系统缺少一个可追溯的恢复方案准备步骤。

恢复方案不能猜测客户可用时间，不能把缺失或过期交通估算当成零缓冲，也不能自行更改正式行程或联系客户。另起一套任务/审批数据库会与既有 Action Center、WorkflowInstance、AgentRun 及 ProactiveSuggestion 重复。

## 决策

1. 新增确定性 APPOINTMENT_RECOVERY planner：只消费 ADR-0044 已验证的 impact assessment，按每个 PlanStop 的 MISSED_WINDOW、IMPACT_UNDETERMINED、UNASSESSED、WINDOW_END_UNKNOWN 和“仍在窗口但余量被消耗”等状态生成恢复动作。
2. 每个恢复动作必须保存来源 Visit / PlanStop、计划与预计到达时刻、延误分钟、判定状态、所需人工输入和下一步；不推断候选预约时间，candidate_windows 固定为空，直到取得客户确认的可用窗口。
3. 每个 Flight Status 建议首次落库时，在同一事务内复用现有 WorkflowInstance + AgentRun 持久化恢复结果；Suggestion Evidence 增加精确 Workflow / AgentRun 引用。重复扫描命中建议去重时，不重复创建 AgentRun。
4. planner 是确定性计算，不调用 LLM、不宣称客户已确认、不自动生成可发送客户文案。运行结果必须标记 human_decision_required；对外沟通仅可在单独的人审和授权流程后执行。
5. 该流程不得修改 Trip、Visit、PlanVersion、PlanStop、PlanTravelLeg 或 FlightSegment，不订票、不改签、不发送消息。后续确需更新预约，必须先取得客户确认的时段，再通过原有计划变更与审批路径生成、验证并人工激活新 PlanVersion。

## 后果

- 航班变化到客户预约恢复有独立、可追溯的 AgentRun 与 Workflow 记录。
- 已错过、可能受影响及无法评估的拜访有不同恢复路径，不会把未知状态混作确定性失败。
- 当前版本不连接客户日历/CRM 可用时段，不生成虚构备选时间，也不进行外部写操作。
- 本 ADR 不新增数据库迁移和 API 端点；恢复简报作为已有 ProactiveSuggestion 的结构化 proposed_action 一部分提供，并由 AgentRun 保存完整输出。
