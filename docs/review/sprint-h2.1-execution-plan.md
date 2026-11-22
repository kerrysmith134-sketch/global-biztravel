# Sprint H2.1 — Durable Research DAG Execution Scheduler

> 基础分支：feat/sprint-h2.0-research-dag-orchestrator  
> 当前状态：设计与工程边界已冻结；实现必须复用既有 BackgroundJob / Worker / Workflow / AgentRun。

## 1. 目标

把 H2.0 已持久化的 ResearchQuestion DAG 变成可恢复的真实执行计划：只启动当前可执行的问题，受限并发地调用现有 Evidence-backed Answer Agent；每个答案仍然是 NEEDS_REVIEW / CONFLICTED 草稿，只有用户接受后才把 Question 变成 ANSWERED 并释放下游依赖。

不新增第二套队列或通用 Agent Runtime；不绕过用户已经确认的来源查询、不自动抓取网页、不自动接受答案或主张。

## 2. 运行时模型

一次 Workspace DAG execution 使用一个现有 WorkflowInstance（workflow_type=market_research_graph_execution），并通过 MarketWorkspaceWorkflow 关联 Workspace。Workflow.payload 固化：

- execution_id / workspace_id；
- 启动时的有向边、Question ID 与图 digest；
- 用户请求的并发上限、最大问题数、时间预算和可选成本上限；
- dispatched / completed / failed / waiting_review 计数；
- started_at / deadline_at / finished_at；
- stop_reason、最后一次调度摘要，以及已取消/仍在执行的子任务统计。

每个节点的执行沿用现有 market_question_answer Workflow + BackgroundJob + AgentRun。答案草拟 Workflow 的 payload 增加 dag_execution_workflow_id 和 question_id，以便完成或进入 DEAD 时唤醒父调度器。节点运行快照可以从 Question、Answer Workflow、BackgroundJob 与 Answer version 计算，不另造 task table。

## 3. 状态语义

调度器仅对同时满足以下条件的问题入队：

- Question 存在于当前 Workspace 的 execution 图快照；
- Question 自身状态不是 ANSWERED / BLOCKED / CANCELLED；
- 所有依赖 Question 均已被用户接受，状态为 ANSWERED；
- 当前节点没有 PENDING / RUNNING 的答案草拟任务；
- 当前节点没有 NEEDS_REVIEW / CONFLICTED 的未决答案版本；
- Workspace 中存在可分析的内容型 Evidence；证据为空时不生成空答案，记录 WAITING_FOR_EVIDENCE；
- execution 未取消，未达到时间/问题数/成本/并发预算。

建议对外展示派生状态：READY、QUEUED、RUNNING、WAITING_FOR_REVIEW、WAITING_FOR_EVIDENCE、WAITING_FOR_DEPENDENCY、BLOCKED、ANSWERED、FAILED、CANCELLED。Question 原始生命周期枚举继续保持现状；派生运行态不把“草稿已生成”伪装成 ANSWERED。

## 4. 触发与恢复

- 用户显式启动 execution，使用 IdempotentWrite 和稳定 execution digest。
- 立即入队一条 market_research_dag_schedule BackgroundJob。
- 调度器通过 Workspace 行锁 + execution Workflow 行锁串行化同 Workspace 图调度；并发度是 execution 级，不依赖进程内 semaphore。
- 调度器按拓扑顺序选择 READY 节点，向现有 enqueue_answer_draft 传入 execution ID；已入队节点在当前事务内记录到 Workflow payload。
- 答案 Agent 成功生成草稿、进入 DEAD/终态失败时，Worker 在同一事务唤醒父执行器；租约回收达到 DEAD 时也会唤醒。ResearchPlan 检索完成、用户选定网页提取完成和 Workspace 新增内容型 Evidence 时同样唤醒。
- 答案 job 可重试失败时沿用 BackgroundJob 重试/租约恢复；若终态 DEAD 或不可重试失败，记录失败节点并唤醒调度器，使其他独立分支继续执行。
- Worker 重启后，执行中的 job 由既有 lease recovery 恢复；execution 本身不依赖内存状态。
- 用户接受某个答案后，Question 变为 ANSWERED；审核事务内唤醒调度器，释放其下游依赖。
- 用户驳回答案不会自动循环重试；只有显式调用 retry node 才能重新排队，且必须满足前置依赖、证据、时间及成本预算。已完成/已取消执行不能重试；失败/部分完成执行可以在原图快照内对失败或被驳回节点发起人工重试。

## 5. 预算与防护

- 服务端配置硬上限：单次 execution 节点数、并发数、执行时间、可选成本上限。
- 用户请求值只能收紧配置上限，不能提高部署级 hard cap。
- 并发数按当前 execution 关联的 PENDING/RUNNING 节点任务从数据库计算，所有写入受 Workspace 行锁保护。
- 成本只使用 LLMGateway 已落库的 LLMInvocation.cost_estimate，遵循部署配置的每千 token 计价单位；不擅自标注 USD。未配置价格时 cost_estimate 返回 null、cost_metering_available=false，不把 UNKNOWN 当作零；此时拒绝用户显式指定成本预算。执行期间失去成本计量时，预算受限批次失败关闭。
- 超时、预算用尽、节点失败、用户取消均写入 execution stop_reason 与 ActivityEvent。
- 已经运行的节点不因调度器停止再入队；取消请求会停止新增任务，并按现有 BackgroundJob 取消能力尝试取消尚未开始的 PENDING 节点；RUNNING 的外部调用只能在安全取消点终止。
- Evidence 选择沿用 Workspace 隔离与现有 24 条上限；本切片不自动执行用户未确认的搜索查询或网页提取。

## 6. API

- POST /v1/market/workspaces/{workspace_id}/research-graph/executions
- GET /v1/market/workspaces/{workspace_id}/research-graph/executions/{execution_id}
- POST /v1/market/workspaces/{workspace_id}/research-graph/executions/{execution_id}:cancel
- POST /v1/market/workspaces/{workspace_id}/research-graph/executions/{execution_id}/questions/{question_id}:retry

所有写接口有幂等键、租户隔离、角色权限、ActivityEvent 和明确状态返回。执行创建时固化图 digest；如果用户后来编辑依赖图，旧 execution 仍针对原始图快照运行，不静默改写历史。

## 7. 验收

必须覆盖：

- 同 Workspace 同时发起两次 execution 的幂等与锁竞争；
- 并发上限在多个 Worker 进程下仍然成立；
- 独立节点可并行，依赖节点必须等到答案被人工接受；
- WAITING_FOR_REVIEW 不会重复排答案；
- 缺 Evidence 时不制造空答案草稿；
- answer job retry、lease recovery、DEAD 与手动 retry 的状态一致；
- 答案审核事务会可靠唤醒下游调度；
- 用户取消后不再新增 node job，已 RUNNING 节点状态可被如实追踪；
- 图被修改后旧 execution 仍使用冻结快照；
- token/cost 计量缺失时 cost_estimate 必须为 null，不能显示为 0；
- 多租户隔离、OpenAPI / iOS 契约、Ruff、MyPy、PostgreSQL、Alembic drift、全量 pytest 与 iOS CI 全绿。

## 8. 严格不在范围内

H2.1 不自动执行外部搜索、不自动选取新的网页来源、不自动验证事实、不自动采纳答案、不把不同域名视为独立出版方，也不自动创建 Customer、Trip、Visit、Opportunity 或对外商务动作。
