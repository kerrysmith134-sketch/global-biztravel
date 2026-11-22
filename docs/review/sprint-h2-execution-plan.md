# Sprint H2 — Intelligence Runtime & Research Orchestration

> 当前切片：H2.0 Workspace Context-aware Research DAG  
> 基础分支：feat/sprint-h1.9-claim-verification-agent  
> 实施分支：feat/sprint-h2.0-research-dag-orchestrator  
> 状态：代码已提交，等待该分支严格 CI 验证；不得提前标记为已验收或已部署。

## 1. H2 的目标

H1 已提供 Workspace、版本化 Evidence、ResearchQuestion、答案草拟、用户选定来源提取和 Claim-level Verification。H2 把这些独立能力编排成可持续推进的研究运行时。

核心闭环：

1. 用户输入任意业务目标和上下文；
2. Planner 结合当前采纳的 Workspace ContextSnapshot，动态生成有依赖关系的 ResearchQuestion DAG；
3. 用户审阅研究问题、优先级、验收标准、预期证据和依赖；
4. 只调度依赖已满足的节点，复用既有 ResearchPlan、BackgroundJob、WorkflowInstance、AgentRun、Evidence 与专业 Agent；
5. 答案与来源保持版本化、可追溯、可审核；
6. 未解决问题、证据缺口和冲突持续反馈到 Workspace，形成下一轮研究。

## 2. H2.0 范围

### 2.1 Workspace Context 成为 Planner 的真实输入

ResearchPlan 绑定 Workspace 时，Planner 输入包括：

- Workspace 名称与目标；
- 当前采纳 ContextSnapshot 的原始输入；
- 已提取结构化上下文、用户覆盖字段、Unknowns 和来源说明；
- 用户本次请求、来源范围和明确选定的 Trip / Customer 上下文（仅在授权范围内）。

ContextSnapshot 通过 Workspace 行锁读取，并记录 snapshot ID、版本和 SHA-256。AgentRun 普通输入日志只保存引用与摘要，不复制 Workspace 原始背景。EXTERNAL_ONLY 仍不允许读取内部 Trip / Customer 记录，但不能丢弃用户主动提供的 Workspace 业务背景。

当前 Context 指针无法解析到同一 Workspace 的当前快照时，规划失败关闭，不回退到任意旧版本。

### 2.2 Planner 输出有向无环研究图

Planner v2 输出 3–8 个动态研究节点，节点字段包括：

- 稳定 key 与问题文本；
- rationale 与优先级；
- acceptance criteria；
- expected evidence；
- depends_on_keys。

服务端将 LLM 输出视为不可信输入，限制最多 12 个节点，校验 key、文本、优先级、依赖引用和环。无效引用会被剔除并产生 planning warning；若整体存在循环依赖，则保留问题节点但清空自动依赖边，等待用户审阅，不把错误图写入执行路径。

### 2.3 复用 Workspace 现有领域模型

- 节点持久化到 MarketResearchQuestion；
- Planner 来源使用 source=AGENT、generated_by_run_id 和 planner_question_key 追溯；
- 预期证据写入 expected_evidence；
- 依赖边写入 MarketQuestionDependency；
- 运行和图生成事件写入现有 MarketActivityEvent；
- 当前 ResearchPlan 仍然是一次研究执行 Workflow，不新增第二套研究计划主表、队列或 Agent Runtime。

新增迁移 0040_market_research_graph 为问题节点增加 Planner provenance 与预期证据字段，并通过同组织复合外键保护 AgentRun 归属。

## 3. 依赖与就绪语义

边的语义固定为：

question_id depends_on_question_id

即当前问题必须等待被引用问题完成并被人工接受（ResearchQuestion 状态为 ANSWERED）后，才具备 READY 条件。

图查询返回：

- nodes 与 edges；
- topological_order；
- READY / WAITING / BLOCKED / ANSWERED / CANCELLED 状态；
- ready_question_ids；
- cycle_detected。

新增依赖写入由 Workspace 行锁串行化；服务端阻止自依赖、跨 Workspace 引用、重复边和循环边。parent_question_id 继续只表达树形展示层级，不混用作执行依赖。

## 4. H2.0 API

- GET /v1/market/workspaces/{workspace_id}/research-graph
- GET /v1/market/workspaces/{workspace_id}/questions/{question_id}/dependencies
- POST /v1/market/workspaces/{workspace_id}/questions/{question_id}/dependencies
- DELETE /v1/market/workspaces/{workspace_id}/questions/{question_id}/dependencies/{dependency_id}

ResearchPlan 响应新增 generated_question_ids；Planner 输出保留 research_graph 与 planning_warnings。

## 5. H2.0 验收门禁

必须验证：

- 当前 ContextSnapshot 的原文、结构化字段和用户覆盖确实进入 Planner 请求；
- EXTERNAL_ONLY 仍不会读取 Trip / Customer，但仍使用 Workspace Context；
- AgentRun 和 Evidence 只保留快照引用与摘要，不重复存放原始 Workspace 输入；
- 生成问题与依赖边在同一事务中写入，且每个节点能回溯 Planner AgentRun；
- 依赖边具有同 Workspace、同组织约束；
- 重复边幂等，反向闭环请求被拒绝；
- 拓扑顺序满足所有依赖先于后继节点；
- ResearchQuestion 只有在答案经过人工接受后才解除下游依赖；
- Ruff、MyPy、OpenAPI、文档、Alembic metadata drift、PostgreSQL 迁移、全量 Backend pytest、SwiftLint、Xcode Build 和 iOS Unit Tests 全部通过。

## 6. 明确留给 H2.1 的范围

H2.0 只生成、持久化和审阅研究图，不把“节点 READY”冒充“Agent 已执行”。后续 H2.1 才实现持久化 DAG 调度器：按 READY 节点建立受限执行批次，使用现有 BackgroundJob 和研究 Worker；支持并发上限、取消、失败重试、死信、成本预算、依赖重算和 Workspace 级进度汇总。每个外部检索仍需用户确认查询，来源全文提取仍需用户明确选定，答案/主张也仍需人工审核。
