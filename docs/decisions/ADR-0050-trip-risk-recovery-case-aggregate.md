# ADR-0050：Trip Risk & Recovery 采用出行级 Case 聚合

## 状态

已接受

## 背景

航班、签证、天气、规划可行性和拜访后续动作由不同领域负责，各自拥有 ProactiveSuggestion、ExternalSignal、VisitFollowUp、WorkflowInstance 与 AgentRun 等事实记录。将这些源记录复制到一个“大任务”或把状态塞进 Workflow JSON，会造成来源真相分裂、重复提醒、状态互相覆盖，也难以回答一趟出差目前有哪些未关闭风险、谁在处理、何时复核以及是否已经升级。

## 决策

1. 引入 `TripRiskCase` 作为 Trip 级跨源风险生命周期投影，并用 `TripRiskCaseEvent` 追加记录每次状态变化。ProactiveSuggestion、VisitFollowUp、AgentRun、ExternalSignal 与 PlanVersion 继续是各自领域的权威事实来源；Case 不复制或接管其业务操作。
2. Case 使用组织 + Trip + 稳定 `case_key` 去重。主动建议按 Suggestion ID 聚合；由 Appointment Recovery AgentRun 派生的 FollowUp 通过其 AgentRun 的 `input.suggestion_id` 回链到同一 Case；无法关联来源建议的人工/现场 FollowUp 保持独立 Case。来源 FollowUp 进入 COMPLETED / DISMISSED 终态后，独立 FollowUp Case 会同步进入 RESOLVED / DISMISSED。
3. Case 生命周期为 OPEN → ACKNOWLEDGED → IN_PROGRESS / WAITING_EXTERNAL / ESCALATED → RESOLVED / DISMISSED。终态只由新来源证据对账自动重开，并保留 REOPENED 事件；客户端不能无来源依据手工重开。每条 Event 写入其对应的 Case Version，数据库触发器拒绝 UPDATE，应用角色无 UPDATE / DELETE 权限。用户写操作必须携带预期 Case Version 与说明；过期 Version 返回冲突。
4. RESOLVED 只接受关闭依据：当前 Trip Version 下重新验证的 ACTIVE Plan、所有相关 FollowUp 完成、Trip 已关闭或有记录的人工确认。DISMISSED 只接受全部相关 FollowUp 已驳回或全部来源建议已确认误报并驳回；两组依据不可交叉使用。不能仅靠前端把风险标成已关闭。
5. HIGH / CRITICAL Case 有明确复核时间。到期且风险仍未关闭时由后台提升为 ESCALATED；高/关键升级通知在同一事务写入现有 Notification 队列，使用用户静音偏好与通知等级策略，并以 Case ID + Version 作为问题级去重键。
6. 主动建议创建/批准/驳回、FollowUp 创建/更新，以及后台主动扫描周期会触发风险投影对账。对账采用稳定来源指纹、组织/Trip 隔离和行锁；重复扫描不重复创建 Case 或 OPEN 事件。
7. 所有对外操作依然受人工授权边界控制：Risk Case 不联系客户、不订票/改签、不修改 Visit/Trip/ACTIVE Plan、不代替审批或报销流程。

## 后果

- 新增迁移 0053，建立 `trip_risk_cases` 与 `trip_risk_case_events`，两表启用 FORCE RLS；Case 使用组织 + Trip 复合外键和唯一 case_key。
- 新增 Trip Risk API：只读列表、事件时间线、显式对账、带 Version 的人工状态转换。
- 复用现有 Notification Worker，不另建推送基础设施。
- Risk Case 从 ProactiveSuggestion、VisitFollowUp 与当前有效 ExternalSignal 构建。ExternalSignal 以 signal_id 为稳定来源身份，可与其主动建议及恢复 FollowUp 合并；天气按精确地点/时间窗评估，航班只消费经验证的 FlightOperationalUpdate 与当前显式机票段绑定。Visa/FX 当前只保留来源与人工核验状态，未建立确定性影响结论。


## H2.26.1 补充：ExternalSignal 作为 Case 的稳定来源身份

- ExternalSignal 不是建议的附属元数据；它是 Provider 事实的权威可追溯来源。Case 以 `external_signal:{signal_id}` 为稳定身份，proactive suggestion 与 VisitFollowUp 在该 Case 上作为派生处置/行动来源。
- 同一 signal 因 payload/计划影响变化而重新生成建议时，应更新原 Case 的来源指纹与影响证据，不应因 suggestion ID 变化而产生第二个开放风险事项。
- Weather impact 仅在 Place ID 精确匹配且时间窗重叠时标记暴露；Flight impact 仅在 ExternalSignal 的规范化运营事件引用、完整性指纹和当前 FlightSegmentPlanBinding 均通过验证时计算。OpenSky telemetry 不构成航空公司运营事实；即使 telemetry 被包装成 ProactiveSuggestion，只要缺少 FlightOperationalUpdate，也不能进入 Trip Risk Case。

## H2.26.2 补充：Recovery Readiness 是证据投影，不是第二套恢复工作流

- Case 通过原有 Suggestion、Appointment Recovery Workflow/AgentRun、VisitFollowUp、Appointment Recovery Reassessment Workflow/AgentRun、REASSESSED Event 与 Evidence 计算恢复就绪度；不新增任务状态真相源，不复制或重写恢复流程。
- Recovery Brief action 必须与人类接受的 FollowUp 按 AgentRun ID、source_index、source_key 一对一对应。生成方案不代表授权、执行或完成；FollowUp 完成也不等同于风险可以关闭。
- Reassessment 必须关联原恢复 AgentRun、当前航班运营事件、PlanVersion、输入指纹及三类结构化 Evidence。窗口陈述保持 PARTIALLY_VERIFIED / externally_verified=false；不把出差人陈述视为客户已确认。
- 当前事件、计划或来源发生变化时旧 Reassessment 标记过期。系统只提供下一步复核/人工提交建议，不自动修改计划、联系客户或关闭 Risk Case。
- Recovery Readiness 完整性失败时 fail closed；只有来源、运行、FollowUp、重评回执及 Evidence 全部匹配，才允许投影为已验证的恢复状态。
## H2.26.3 补充：ACTIVE Plan 还必须匹配当前 Trip 输入版本

- `PlanVersion.status == ACTIVE` 仅表示生命周期状态；若 `PlanVersion.trip_version != Trip.version`，该 Plan 不再代表当前行程输入。外部天气/航班影响评估必须返回 `STALE_ACTIVE_PLAN_INPUT`，不能使用旧 PlanStop 或 PlanTravelLeg 继续推断当前预约影响。
- Recovery Brief、Workflow、AgentRun 和 Reassessment 必须携带 Plan/Trip 版本指纹。Trip 输入变化后，既有 Brief/Reassessment 自动变为 stale，需要先重建并激活当前计划，再重新评估。
- 不新增 PlanVersion 状态，也不自动 SUPERSEDE/激活计划；继续由现有 Plan Change / planning workflow 管理计划生命周期。

## H2.26.4 补充：Recovery Brief 必须能从当前权威事实重建

- Suggestion 与 AgentRun 的两份 JSON 输出一致仅能证明副本一致，不能单独证明动作清单真实来自当前影响事实。Risk Readiness 必须使用当前可验证的 FlightOperationalUpdate / ExternalSignal / Plan impact 和原始 generated_at 重新运行确定性 Brief builder，并比较完整规范化输出。
- Agent 算法版本变化使 Brief 过期；generated_at 无效或同版本下不能重现完整 Brief 则为完整性失败。不能让对两个副本的同步篡改绕过核验。
- Provider 时间戳容差统一为最多五分钟的有限时钟偏差，超过容差的未来信号不进入当前风险聚合；有效结束时间始终按当前真实时钟判断。
- Case Event API 以 case_version 表示因果顺序；created_at 不作为审计事件的主排序键。

## H2.26.5 补充：PLAN_CHANGE 的受理、候选生成与激活必须分阶段

- PLAN_CHANGE Workflow.status=ACCEPTED 仅代表请求已受理并创建/关联 plan_trip BackgroundJob；只有 Job.status=SUCCEEDED 且 result.plan_version_id 指向同组织、同 Trip、匹配 Trip Version 的 PlanVersion，才代表候选计划已生成。
- 候选计划 DRAFT 仍须由出差人通过现有 plans/:activate API 人工激活。激活事实必须同时满足 PlanVersion.status=ACTIVE 与 Trip.active_plan_version_id 指向该版本。
- 新计划激活后，旧 Reassessment 的 Plan/Trip 版本快照自然过期；必须基于当前 ACTIVE Plan 再次重评。计划激活本身不能自动完成 VisitFollowUp，也不能自动关闭 TripRiskCase。
- 对 Workflow、BackgroundJob、PlanVersion 的来源、组织/Trip、版本、操作和预约时间窗进行一致性核验；Job 失败、缺少结果计划、计划过期或关联不一致时分别投影为失败/过期/完整性异常状态。
- iOS 只展示服务端阶段投影与证据引用，不自行推断候选计划已激活，也不以 Job 成功或计划激活代替客户独立确认。
- Plan Change 的 Job.result.plan_version_id 必须反向绑定到 PlanVersion.job_id；候选 PlanVersion 的 organization、Trip、trip_version 与 Job payload/result 一致。候选 DRAFT 只有在原基础 Plan 仍为当前 ACTIVE 时才可提交人工激活；激活后只有当前 Trip.active_plan_version_id 指向候选 PlanVersion 才能投影为已激活。