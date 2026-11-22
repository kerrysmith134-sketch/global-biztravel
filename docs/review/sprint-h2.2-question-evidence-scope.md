# Sprint H2.2 — Question-Level Research & Evidence Scope

> 基础分支：`feat/sprint-h2.1-dag-execution-scheduler`  
> 产品目标：让 ResearchQuestion 能独立获得、审核和使用与自身问题直接相关的证据，同时不污染 Workspace 其他问题。

## 1. H2.1 暴露出的缺口

H2.1 已有持久化 DAG scheduler，但节点能否启动答案草稿目前基于 Workspace 全局内容型 Evidence 判断。这样会产生两个错误语义：

1. Workspace 只要有任意一条来源内容，所有 READY 问题都可能使用它，即使来源与具体问题无关。
2. 没有来源内容时，问题只能停留在 WAITING_FOR_EVIDENCE；没有一个问题级、可审阅、可执行的检索闭环把它推进到有证据状态。

H2.2 将证据从“Workspace 里有来源”提升为“这个来源是否可供这个研究问题使用”，并复用现有 ResearchPlan / ResearchSearch Provider / Evidence / MarketEvidenceDerivation，不再创建第二套搜索栈。

## 2. 核心产品闭环

~~~text
ResearchQuestion
  → Create question-scoped ResearchPlan
  → Planner proposes search queries
  → User edits / approves exact queries
  → Existing ResearchSearch Provider executes asynchronously
  → Immutable Workspace Evidence + question provenance link
  → Optional user-selected page extraction
  → Derived page Evidence inherits the original question scope
  → DAG scheduler sees this question's evidence as READY
  → Existing answer-draft Agent
  → User reviews / accepts answer
  → Downstream DAG dependencies are released
~~~

重要边界：Planner 只提出查询；外部检索只执行用户明确确认的查询；搜索摘要仍是 UNVERIFIED；网页正文提取仍由用户选择来源后触发；LLM 答案仍须人工审核。

## 3. 逻辑数据模型

继续使用 `MarketResearchPlanEvidence`，增加可空 `question_id`：

- `question_id IS NULL`：Workspace 级共享研究来源；兼容旧版 ResearchPlan 与用户主动添加的 Workspace Evidence。
- `question_id = Q`：该 ResearchPlan 的检索结果只作为 Q 的定向证据。
- 同一 Evidence 可以被多个问题使用；每个目标问题都有独立 provenance relation，不复制 Evidence 内容。
- 外部来源正文提取生成新的 Evidence 版本，通过 `MarketEvidenceDerivation` 找到原始搜索证据，并继承其问题可见范围。
- 外键必须同时约束 organization_id + workspace_id + question_id；问题、Workspace 或关联 ResearchPlan 被删除时，关系按既定级联策略处理。Evidence 仍采用 RESTRICT，不能因删除一个研究问题而误删来源版本。

数据库迁移将已有行的 `question_id` 保持 NULL，因此现有行为向后兼容。

## 4. Evidence 可见范围规则

给定研究问题 Q，答案 Agent 可以读取：

1. 用户在 Workspace 级主动添加、且未被定向关联到其他问题的内容型 Evidence；
2. `MarketResearchPlanEvidence.question_id = NULL` 的共享检索 Evidence；
3. `question_id = Q` 的问题级检索 Evidence；
4. 从以上任一来源派生的网页正文 Evidence（通过 EvidenceDerivation 继承来源范围）。

问题 A 定向 Evidence 不得被问题 B 读取，除非用户显式把同一 Evidence 关联给 B。仅有相同 Workspace、相同来源 URL 或相同 ResearchPlan 不能自动意味着跨问题共享。

对有问题定向关系的 Evidence，缺少对应关联的其他问题不得因其位于 Workspace 表中就自动访问。

## 5. ResearchPlan 扩展

沿用现有 `POST /v1/research/plans` 和 `POST /v1/research/plans/{plan_id}:execute`：

- `ResearchPlanCreate.target_question_id` 可选；设置时必须同时提供 workspace_id，并校验该问题属于同一组织和 Workspace。
- 定向 ResearchPlan 将问题文本、rationale、acceptance criteria、expected evidence 以及当前被采纳的 Workspace ContextSnapshot 一并交给 Planner。
- 定向计划只生成搜索查询，不再把 Planner 输出的整个 research_graph 追加为新问题；避免每个 Question 的检索计划反向扩张 DAG。
- `ResearchPlanRead` 返回 target_question_id，Workspace 研究计划列表可按问题定位。
- Query list 仍由用户确认；execute endpoint 继续校验查询数量、digest、Provider 配置和幂等键。
- 检索完成后写入 Workspace 版本化 Evidence，并在 `MarketResearchPlanEvidence` 中保存 question_id、精确搜索查询、query SHA-256、采集 AgentRun。

## 6. Scheduler 与 Answer Agent 改造

- `_node_statuses` 按每个 Question 查询其可见内容型 Evidence；不能再用 Workspace 全局 Evidence 数作为所有节点的 READY 判据。
- `enqueue_answer_draft` 和 Worker 执行时的 `load_answer_draft_inputs` 使用同一 question-scoped Evidence predicate，避免 API 预检查与 Worker 二次校验范围不一致。
- 当问题专属检索完成、网页提取完成或用户补充 Workspace 共享 Evidence 后，继续通过现有 wake-up 机制触发 DAG scheduler。
- 只有当前 Question 有可分析正文或摘要时，答案 Agent 才能启动；最多仍受现有每个答案 24 条 Evidence 上限约束。
- 证据被拒绝、来源变更或关系解除后，新的执行只会按当前关联范围选择证据；已有 Answer 版本保留其不可变 Evidence 引用。

## 7. 安全与完整性

- 所有新写入经过既有角色权限、IdempotentWrite、组织 RLS 与 Workspace ownership 校验。
- 不能仅凭 question_id 读取其他 Workspace 或组织的数据；数据库复合外键与服务层都要验证。
- ResearchPlan 的 query digest 必须由执行任务重算并与工作流 payload / Job payload 同时匹配。
- 搜索结果与网页正文仍标记 UNVERIFIED；来源内容视为不可信数据，提示注入不得改变 Agent 任务。
- 证据关联写入审计事件；去重不改变已有 Evidence 原文和 content hash。
- 旧版 question_id=NULL 数据继续视为共享来源，不做自动猜测式迁移。

## 8. API / 测试验收

至少覆盖：

- 创建问题级 ResearchPlan 不会额外创建新的 ResearchQuestion；
- 目标问题不属于当前 Workspace 时拒绝创建；
- 只有用户提交的 reviewed_search_queries 才能启动 Provider；
- 检索任务保存 question_id/query/hash/run provenance；
- 一条 Evidence 可以关联多个问题，但未关联的问题无法读取定向证据；
- Workspace 共享 Evidence 仍可被所有问题读取；
- 派生网页正文正确继承来源问题范围；
- H2.1 scheduler：一个问题有 Evidence 而另一个没有时，只允许前者进入 READY；
- answer enqueue 与 Worker 执行时的证据范围一致；
- 租户隔离、RLS、并发幂等、Evidence RESTRICT 删除语义；
- OpenAPI / iOS contracts、Ruff、MyPy、docs-check、PostgreSQL、Alembic drift、全量 pytest、SwiftLint、Xcode build 和 XCTest 全绿。

## 9. 不在本 Sprint 范围

- 不把搜索摘要自动升级为 VERIFIED；
- 不自动采纳答案、Insight 或 Opportunity；
- 不做跨行业写死查询模板；
- 不自动创建 Customer、修改 Trip、发送邮件、询价或预订；
- 不做无来源的模型记忆补全；
- 不复制已有 Workspace Evidence 来实现问题隔离，必须使用显式 provenance link。
