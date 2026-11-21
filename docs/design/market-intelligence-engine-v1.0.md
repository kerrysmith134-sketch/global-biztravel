# Market Intelligence Engine v1.0
> 状态：Sprint H 设计基线（待代码审计后冻结）  
> 适用范围：通用全球商务差旅产品，不绑定任何公司、行业或产品  
> 设计日期：2026-11-21  
> 关联基线：`docs/design/global-business-travel-os-design-v2.0.md`

---

## 1. 目标与非目标

### 1.1 目标

Market Intelligence Engine（MIE）把用户提供的业务背景、市场目标和研究问题，转化为**可追溯的市场证据、可解释的机会判断和可执行的商务行动**，并通过真实拜访结果持续修正市场认知。

它必须适用于不同国家、行业、商业模式和用户角色。系统不预设用户属于某个行业，也不把行业分类、客户类型、竞争维度或市场进入框架固化为代码枚举。

核心链路：

~~~text
用户输入业务背景 / 探索目标
  → Context Extraction
  → Research Planning
  → Evidence Collection & Verification
  → Entity Resolution
  → Insight Synthesis
  → Opportunity Qualification
  → User Decision
  → Customer / Trip / Visit Execution
  → Outcome Feedback
~~~

### 1.2 产品边界

MIE 是 Global Business Travel OS 的可选智能能力，不取代现有 Trip 核心流程。

支持三种入口：

1. **直接出差**：用户直接创建 Trip，不需要先做市场研究。
2. **先研究、后出差**：用户创建 Market Workspace，识别机会后再创建或关联 Trip。
3. **围绕既有出差补充研究**：在 Trip 内启动某个市场、城市、客户或行业问题的研究。

市场研究不是每次出差的强制前置步骤。没有足够信息时，系统应明确列出未知项并继续可执行的部分，不得编造业务背景或市场事实。

### 1.3 非目标

本版本不承诺：

- 自动完成交易、签约、报价、付款或对外发送商务消息。
- 在没有真实数据源或授权连接器时声称已完成市场调查。
- 用模型生成的市场规模、客户名单或采购意向替代可核验证据。
- 把 AI 生成的候选企业直接写入正式 Customer 主数据。
- 用一个“市场潜力总分”掩盖证据缺失、来源冲突和主观假设。

对外发送、创建正式客户、改变正式行程或执行其他有业务影响的写操作，必须复用现有授权、人审、幂等和审计机制。

---

## 2. 与现有系统架构的关系

本设计是对现有设计 v2.0 的增量扩展，不另起一套技术栈或平行业务模型。

### 2.1 必须复用的现有能力

| 已有能力 | MIE 的复用方式 | 禁止的重复建设 |
|---|---|---|
| FastAPI 模块化单体与业务用例层 | 承载 Market Workspace、研究和机会用例 | 在路由函数中写业务逻辑 |
| PostgreSQL 16 / PostGIS / pgvector | 存储工作空间、市场实体、关联关系及可检索内容 | 为市场智能另建独立数据库作为第一选择 |
| RLS / 组织隔离 | 所有新组织级记录沿用现有组织隔离机制 | 只在 API 层过滤组织 ID |
| WorkflowInstance / AgentRun | 承载研究工作流、Agent 执行、状态和追踪 | 再造一套不兼容的 AgentRun/Workflow |
| Evidence | 复用现有证据持久化和引用能力；必要时做向前兼容的增量扩展 | 创建语义重复的第二张 Evidence 主表 |
| LLM Gateway / prompt 版本 / 调用审计 | 负责模型路由、结构化输出、成本记录和降级 | 各 Agent 自行接模型 SDK |
| Tool Registry / 业务用例 | Agent 通过声明式工具访问系统能力 | Agent 直连数据库或绕过业务用例 |
| Outbox / Background Jobs / Workers | 处理异步检索、长任务、重试和恢复 | 把长时间研究放在 HTTP 请求生命周期内 |
| Customers / Places / Trips / Visits | 作为正式客户、地点、出差和拜访的现有真相源 | 另造 TargetAccount、Mission、VisitOutcome 主对象 |
| Meeting Minutes / Trip Outcome / Memory | 获取真实拜访反馈和历史经验 | 把会议纪要摘要误当成已验证的商业事实 |

> 实施前必须逐字段审查当前模型、迁移和服务代码。上表确认的是复用原则，不代表当前 Evidence 或 Customer 表已经具备本文件提出的全部字段。

### 2.2 ResearchPlan 与 MarketWorkspace 的职责边界

当前用户意图研究能力中的 ResearchPlan 是**一次研究请求与执行运行**，由现有 WorkflowInstance（workflow_type = research_plan）、AgentRun、BackgroundJob 和 Evidence 表达；不新增独立 ResearchPlan 主表，也不另建搜索 Worker。

MarketWorkspace 是**跨多次研究持续存在的工作空间**，保存上下文版本、研究问题、候选实体、洞察和机会。一个 Workspace 可以关联多个现有 ResearchPlan Workflow；一个 ResearchPlan 也允许不属于任何 Workspace，保持单次研究与既有入口兼容。

二者通过 MarketWorkspaceWorkflow 关系连接，必须在数据库层保证 Workspace 与 WorkflowInstance 属于同一 organization。ResearchPlan 的外部检索继续遵循用户确认查询、Provider 显式配置、结果初始未核验的既有边界；MarketWorkspace 不得绕过这些规则或复制一套检索执行器。

### 2.2 技术实现约束

- 后端继续使用仓库现有 Python / FastAPI / Pydantic / SQLAlchemy / Alembic 体系；不得引入 TypeScript 数据模型作为后端契约真相。
- `contracts/openapi.yaml` 继续作为 iOS 与后端之间的 API 契约基准，按现有流程从服务端生成并执行契约漂移检查。
- iOS 继续采用 SwiftUI；新增页面必须使用现有 Design System 令牌与组件，不创建第二套视觉系统。
- 模块化单体 + 持久化 Worker 是当前架构基线；没有容量证据前不拆微服务。
- 所有新的组织级实体必须完成 RLS、跨租户测试和审计设计。

---

## 3. 通用业务上下文

### 3.1 自然语言是输入起点，不是唯一数据结构

用户可以描述：
- 自己提供的产品或服务；
- 希望进入的国家、地区或城市；
- 想接触的客户、合作伙伴或项目类型；
- 希望验证的市场假设；
- 预算、时间、渠道、合规等约束；
- 本次研究最终要支持的商业决策。

系统先保留完整原始输入，再抽取结构化上下文。结构化结果必须允许用户修改，并能追溯到来源。

建议的逻辑字段：

| 字段 | 说明 |
|---|---|
| raw_input | 用户原始描述，不得被模型输出覆盖 |
| offerings | 产品、服务、解决方案或能力；允许多个 |
| business_models | 直销、渠道、订阅、项目制等业务模式；按用户实际描述动态扩展 |
| industries | 一个或多个行业标签；动态值，不是代码枚举 |
| customer_segments | 用户描述的目标客户群；动态值，不是固定客户类型 |
| value_propositions | 用户认可的价值主张 |
| target_markets | 国家、区域、城市或其他市场范围 |
| goals | 本次希望达成的研究或商务目标 |
| constraints | 时间、预算、资源、准入、合规等约束 |
| assumptions | 尚未验证的用户假设或模型推断 |
| unknowns | 缺失且不能擅自补全的信息 |
| provenance | 每个结构化字段的来源：用户输入、用户修订、外部证据或模型推断 |
| extraction_version | 抽取 schema / prompt 版本 |

### 3.2 动态分类与扩展属性

不允许把行业、产品类别、客户类型、采购角色、竞争类别或商业模式写成有限的代码枚举。

系统可以维护可演进的通用基础标签，但业务分类应支持：
- 动态新增和合并；
- 不同语言的显示名称；
- 原始标签与规范化标签并存；
- 标签来源与置信度；
- 用户修订和分类版本；
- 行业特定扩展属性，而不污染通用核心表。

标准化不等于丢弃用户原文。原始值必须保留，以便纠错、迁移和重新抽取。

### 3.3 信息不足时的行为

- 缺少关键背景时提出最少量、最有价值的澄清问题。
- 可以继续的研究先继续，并将依赖缺失信息的部分标为 BLOCKED 或 INSUFFICIENT_CONTEXT。
- Unknown 不得转换成默认行业、默认国家或虚构客户规模。
- 模型置信度不能代替证据质量。

---

## 4. 领域对象与数据关系

以下是**逻辑领域模型**。物理表设计必须先审查现有模型和迁移，再决定新增表、扩展现有表还是使用关联表。

### 4.1 核心对象

| 逻辑对象 | 职责 | 与现有领域的关系 |
|---|---|---|
| MarketWorkspace | 一次有边界的市场探索工作空间，记录目标、上下文快照和研究状态 | 可以独立存在，也可关联一个或多个 Trip |
| BusinessContextSnapshot | 某次研究使用的业务上下文快照，保留当时的输入与用户修订 | 不等于长期用户画像；每次研究可有不同上下文 |
| ResearchQuestion | 用户问题或 Planner 动态拆解出的可验证研究问题 | 由持久化工作流管理状态 |
| MarketEntity | 研究中发现的公司、人物、产品、机构、地点、项目等候选实体 | 不是正式 Customer 主数据 |
| MarketInsight | 基于证据、假设和反证形成的市场判断 | 可随新证据被修订或撤销 |
| MarketOpportunity | 与市场实体相关的潜在商业机会及其评估依据 | 被用户确认后可关联现有 Customer / Trip / Visit |
| Evidence | 原始来源、观察内容、来源时间和证据状态 | 复用现有 Evidence 能力，先审查是否需扩展 |
| EntityLink | MarketEntity 与正式 Customer / Place 等业务对象之间的匹配或关联 | 明确记录匹配状态与确认来源 |
| ResearchRun | 一次研究工作流的运行上下文 | 通过现有 WorkflowInstance / AgentRun 表达，不另造运行时主对象 |

### 4.2 关系

~~~text
MarketWorkspace
  ├─ BusinessContextSnapshot
  ├─ ResearchQuestion*
  ├─ MarketEntity*
  │    └─ EntityLink → Customer / Place / other existing records
  ├─ Evidence*                         (reuse existing evidence store)
  ├─ MarketInsight*
  │    ├─ supporting Evidence*
  │    └─ contradicting Evidence*
  └─ MarketOpportunity*
       ├─ related MarketEntity*
       ├─ supporting / contradicting Evidence*
       └─ accepted action → Customer / Trip / Visit workflow

Trip
  ├─ optional MarketWorkspace link
  └─ existing Visits / Visit Records / Meeting Minutes / Trip Outcome
       └─ feedback → MarketInsight / MarketOpportunity / Memory
~~~

一个客户可能关联多个机会；一个机会也可能涉及多个企业、渠道方或合作伙伴。数据关系不得被简化成“一个企业等于一个机会”。

### 4.3 正式主数据边界

- MarketEntity 是候选市场实体；Customer 是现有正式客户对象。
- 同一公司可能在不同来源下出现多个名称、域名或分支机构，必须先做实体解析，不可仅凭名称自动合并。
- 把候选实体提升为正式 Customer，必须经过用户确认，并通过现有 Customer 用例完成。
- EntityLink 保留匹配状态（候选、已匹配、用户确认、冲突、拒绝）、匹配依据、确认人和时间。
- 已有 Customer 优先复用；不得因市场研究重复创建客户。
- Trip 继续代表真实差旅任务；本设计不新建与 Trip 重叠的 Mission 主实体。
- Visit Records / Meeting Minutes / Trip Outcome 继续承载实际拜访与复盘事实；不新建平行 VisitOutcome 主实体。

### 4.4 Opportunity 生命周期

建议的业务状态（最终以 API schema 与服务端校验为准）：

~~~text
DISCOVERED
  → RESEARCHING
  → EVIDENCE_READY
  → QUALIFICATION_PENDING
  → QUALIFIED
  → ACTION_PLANNED
  → {WON | LOST | DISQUALIFIED | ON_HOLD}
~~~

状态迁移必须有理由、证据引用、发生时间和操作者。模型可以提出状态迁移建议，但不能仅凭自评把机会标为 QUALIFIED 或 WON。

---

## 5. Evidence & Insight：可信市场认知

### 5.1 证据与结论必须分层

严格区分：
1. **Source**：来源本身，例如官方统计、企业网站、公开报告或用户上传材料。
2. **Observation**：从来源中实际提取的事实片段或可复核观察。
3. **Evidence**：该观察对某个研究问题、实体或判断的支持/反驳关系。
4. **Insight**：综合多条证据与假设后形成的判断。
5. **Recommendation**：基于当前判断提出的下一步行动。

AI 总结不能替代原始来源和观察记录。

### 5.2 Evidence 最小逻辑字段

在审查现有 Evidence 模型后，确认是否需要以兼容方式补齐以下信息：
- source_type：来源类别；
- source_title / publisher；
- source_url 或受控文件引用；
- published_at（未知时为空）；
- retrieved_at；
- content_hash；
- observation：来源实际支持的内容或短摘录；
- related_entity_ids；
- supports / contradicts 关联；
- verification_status；
- quality_dimensions；
- created_by_run_id / llm_invocation_id（适用时）。

不得虚构来源发布日期、作者或 URL。对无法提供可复核链接的内部资料，应明确标注来源类型和访问边界。

### 5.3 验证与质量评估

至少分别评估：

| 维度 | 评估问题 |
|---|---|
| Authority | 来源是否具备相应权威性？ |
| Freshness | 该类信息在当前时间是否仍有效？ |
| Specificity | 来源是否直接支持当前问题，而非仅有相关性？ |
| Corroboration | 是否有独立来源交叉验证？ |
| Consistency | 是否与其他证据或已知事实冲突？ |

不建议首版把上述维度压成一个貌似精确的“真实性百分比”。优先输出 High / Medium / Low / Insufficient，并展示维度依据。

信息时效按数据类型定义，不使用一个全局 TTL。例如企业是否仍营业、联系人职位、市场法规和历史宏观统计，其过期语义不同。

### 5.4 冲突与不确定性

- 支持证据和反证必须同时保存。
- 证据冲突时，Insight 进入 CONFLICTED 或等价状态，并指出冲突点。
- 缺少证据时标记 INSUFFICIENT_EVIDENCE，不能靠模型语言补成确定结论。
- 事实、用户陈述、来源观察、AI 推断必须有明确标识。
- 任何重要结论都必须可以从 UI 反向打开支持证据、反证和来源。
- 来源快照的存储应尊重版权、访问权限和数据留存要求；保存短摘录、哈希和来源链接优先于无差别复制全文。

### 5.5 外部内容安全

网页、文件、邮件和第三方 API 返回内容均视为不可信数据：
- 检索内容不能作为系统指令执行；
- 禁止按网页内容泄露 prompt、密钥或其他租户数据；
- 来源解析与工具调用权限隔离；
- 对下载、文件类型、大小、重定向和内网地址访问设安全限制；
- 所有检索与写操作进入运行审计；
- 外部来源无法访问时显式记录失败，不得用模型记忆伪装成实时检索。

---

## 6. Agent 与工作流

### 6.1 编排原则

采用现有 Supervisor / Workflow / AgentRun / Worker 运行基座。专业 Agent 是可测试的任务执行单元，不是彼此自由聊天的角色集合。

逻辑职责如下：

| Agent / 阶段 | 职责 | 交付物 |
|---|---|---|
| Context Extraction | 解析用户输入、识别歧义和缺失项 | BusinessContextSnapshot + unknowns |
| Research Planner | 从用户目标动态推导问题、任务和依赖 | ResearchPlan / ResearchQuestion |
| Research | 使用已授权工具发现资料与候选实体 | source observations + candidate entities |
| Evidence Validation | 检查来源质量、时效、矛盾和问题覆盖 | Evidence status + gaps |
| Entity Resolution | 归一化名称、域名、地区与实体关系 | MarketEntity + match candidates |
| Insight Synthesis | 把证据、反证和假设组织成判断 | MarketInsight |
| Opportunity Qualification | 评估需求、匹配、可达性、风险和下一步 | MarketOpportunity proposal |
| Strategy | 将已验证机会转为市场进入/拜访策略 | ranked action plan |
| Trip Integration | 经用户确认把机会链接至 Customer / Trip / Visit | approved business action |

### 6.2 动态研究计划

Planner 应从用户上下文推导研究问题，而不是套用固定行业模板。

例如用户描述“工业设备供应商寻找欧洲代理商”时，研究问题可能包括渠道结构、当地认证、服务能力、目标经销商和竞争品牌。

用户描述“企业软件进入东南亚中型企业市场”时，研究问题则可能包括客户规模定义、采购决策流程、部署与数据合规、竞争替代方案和系统集成生态。

两者应生成不同的研究图谱。通用系统提供研究能力与工具，不提供写死的行业答案。

每个 ResearchQuestion 至少具备：
- 明确的问题文本；
- 产生原因和所属目标；
- 优先级；
- 期望证据类型或验收条件；
- 依赖项；
- 当前状态；
- 结果引用；
- 失败或无法验证原因。

### 6.3 运行时要求

- 研究任务通过后台持久化执行，支持中断恢复和取消。
- 对独立任务并行执行，对有依赖的任务按图执行。
- 同一来源和同一输入支持幂等，避免重复抓取、重复建实体。
- 每个 AgentRun 记录输入摘要/哈希、输出结构、模型、prompt 版本、工具调用、token/成本、延迟和错误。
- 结构化输出必须通过 Pydantic / JSON Schema 校验；失败有界重试，之后显式降级。
- 限制单次 Workspace 的时间、模型成本、工具调用数和并发度。
- 研究完成度根据问题覆盖、证据质量和未解决缺口判断，不能以“Agent 已结束”作为完成标准。
- LLM 不可用时，已有 Trip 创建、客户浏览和规划能力必须继续工作。

### 6.4 工具与权限

工具分为只读研究工具、候选写入工具和正式业务写工具：

| 工具类别 | 示例 | 权限要求 |
|---|---|---|
| 只读 | 搜索公开资料、读取已授权客户资料、读取 Trip | 明确 read scope、限速、审计 |
| 候选写入 | 保存候选实体、证据、Insight 草稿 | 工作空间写权限、幂等、可撤销 |
| 正式业务写入 | 创建/合并 Customer、修改 Trip、创建拜访 | 通过现有业务用例、人审与授权；不可由 Agent 直连 DB |
| 对外动作 | 发送邮件、提交询价、预订等 | 单独审批策略；本版本不自动执行 |

Agent 工具不能扩大当前用户的权限。Workspace 之外的数据不能因为 Agent 研究需要而被隐式读取。

---

## 7. Opportunity 评估方法

### 7.1 分维度解释，禁止黑箱总分

建议的通用评估维度：
- Strategic Fit：与用户产品、能力和目标是否匹配；
- Demand Evidence：是否存在可核验的需求信号；
- Commercial Potential：潜在价值及其估算依据；
- Accessibility：能否找到决策链、渠道或有效触达方式；
- Competition：竞争、替代品和进入壁垒；
- Timing：法规、市场变化、采购窗口等时机因素；
- Execution Cost：获取机会所需时间、投入和资源。

每个维度必须有 value / rationale / evidence_refs / unknowns。允许某些维度为 Unknown，不得把缺失值当作零或中位数悄悄参与排序。

### 7.2 评分与排序

- 第一版采用可解释的规则/权重配置；权重需能按工作空间调整并记录版本。
- LLM 可以提议维度和权重，但不能在无记录的情况下改变正式评分规则。
- 排序输出同时展示硬性门槛、证据充分度和不确定性。
- 评分模型必须通过跨行业样本校准；未校准前标记为“辅助排序”，不得显示成预测成交概率。

### 7.3 资格状态

候选发现、已研究、已验证、已合格、已接触和已转化是不同状态。状态升级需要对应依据：

- DISCOVERED：只代表系统或用户发现了候选；
- RESEARCHING：正在研究；
- EVIDENCE_READY：达到最低证据覆盖门槛；
- QUALIFICATION_PENDING：等待用户确认是否符合目标；
- QUALIFIED：符合用户设定的标准，且已由用户或授权业务规则确认；
- ACTION_PLANNED：已形成下一步商务行动；
- WON / LOST / DISQUALIFIED / ON_HOLD：必须有业务事件或明确原因支撑。

“发现企业”不等于“发现商机”；“企业网站存在”不等于“有采购需求”；模型高置信度不等于 Verified。

---

## 8. 用户体验与信息架构

### 8.1 交互原则

Market Intelligence 应是商务决策工作区，不是聊天记录或长篇 AI 报告阅读器。

主界面优先回答：
1. 当前研究目标是什么？
2. 已经发现哪些可靠事实？
3. 哪些结论仍然不确定？
4. 哪些机会值得用户决策？
5. 下一步可以执行什么？

### 8.2 推荐视图

| 视图 | 核心内容 |
|---|---|
| Market Overview | 研究目标、关键发现、机会摘要、数据缺口、待决策事项 |
| Research Workspace | 问题树、运行状态、发现结果、证据与反证 |
| Market Map | 国家/城市/企业/地点/机会空间分布，与列表双向联动 |
| Opportunity Board | 机会状态、维度评估、证据覆盖、风险、下一步 |
| Trip Builder | 将已确认目标加入现有 Trip，或创建新的 Trip |

每个页面应保持信息密度、留白、视觉层级和操作模式一致，使用现有 Design System。首页不以 KPI 瓷砖堆砌，不将所有研究细节铺成连续文字。

### 8.3 AI 对话的定位

对话是用户表达目标、补充信息和追问依据的输入方式，不是唯一工作界面。

重要输出应沉淀为结构化实体卡片、Evidence、Insight 或 Opportunity，并且能从卡片回到来源。删除对话不应删除已持久化的研究结果和工作流（沿用现有对话与任务分离原则）。

---

## 9. 端到端验收标准

验收必须覆盖真实服务端、数据库、后台 Worker 和 iOS 契约。不能用静态页面、假 API 或预置“成功”文案代替。

### 9.1 跨行业泛化场景

至少建立以下五种独立夹具，不将它们写成运行时行业枚举：

| 场景 | 用户目标 | 主要验证点 |
|---|---|---|
| 工业设备 | 寻找欧洲渠道合作伙伴 | 渠道结构、实体识别、认证问题 |
| 企业软件 | 寻找东南亚中型企业客户 | 客户分层、采购流程、数据合规 |
| 医疗器械 | 研究拉美经销渠道 | 国家差异、监管来源、证据时效 |
| 商业咨询 | 在中东发现项目机会 | 项目/机构/决策人实体、触达依据 |
| 消费品 | 研究非洲零售渠道 | 渠道层级、地区覆盖、分销关系 |

### 9.2 必须通过的行为验收

- 用户原始输入始终保留；AI 抽取结果可编辑并可追溯。
- 至少五类业务输入生成差异化 ResearchQuestion，而不是相同模板替换行业名。
- 信息不足时显示 Unknown / Clarification，不虚构业务背景。
- 每个外部事实陈述可打开来源或明确显示不可验证。
- 来源发布日期未知时不伪造日期。
- 证据冲突能保留两侧来源并降低结论确定性。
- 同一企业多种名称能够提出去重候选，不自动错误合并。
- 已有 Customer 能关联而非重复创建。
- Opportunity 状态升级必须经过规则或用户确认。
- 研究长任务可取消、可恢复、重复请求幂等。
- AgentRun / LLM 调用 / 工具执行可以追踪。
- RLS 阻止跨组织读取与写入。
- 用户不启动市场研究时，现有创建 Trip、规划、地图、现场记录和离线流程仍可用。

### 9.3 安全和对抗测试

- 外部网页包含 prompt injection 指令，Agent 不执行其中指令。
- 恶意链接、内网地址、超大文件和异常重定向被安全策略拦截。
- 组织 A 的研究任务不得访问组织 B 的 Customer、Trip、Evidence。
- 用户无写权限时，Agent 不得通过工具绕过权限创建或修改正式对象。
- 同一工作流重复投递不得生成重复实体、证据和商机记录。
- 取消或失败的研究任务不得被标记为成功完成。
- 模型输出不符合 schema 时走有界重试和明确降级。

### 9.4 质量指标

首版优先度量而不虚构目标值：
- ResearchQuestion 完成率与阻塞原因；
- Evidence 来源可追溯率；
- 证据新鲜度与冲突率；
- Entity 去重候选的用户确认准确率；
- Opportunity 资格确认率；
- 用户修订 AI 抽取结果的比例；
- 工作流成功率、恢复率、重试率、重复写入拦截率；
- 每个完成 Workspace 的模型成本和总耗时。

待有真实基线数据后再制定 SLO 和质量阈值。

---

## 10. 演进分期

### Sprint H1 — Domain Foundation

- 审计现有 Customer / Place / Trip / Visit / Evidence / WorkflowInstance / AgentRun / Memory 模型与迁移。
- 冻结 MarketWorkspace、BusinessContextSnapshot、ResearchQuestion、MarketEntity、MarketInsight、MarketOpportunity 的逻辑契约。
- 确认可复用 Evidence 还是做最小兼容扩展。
- 设计组织隔离、唯一约束、幂等、审计、状态迁移和删除语义。
- 新增后端 Pydantic schema、领域服务、应用用例和 API；API 契约纳入 `contracts/openapi.yaml`。
- 完成 PostgreSQL 集成测试、RLS 测试、契约样本和 iOS 模型解码测试。
- 不在 H1 中实现完整外部研究爬取能力，也不做自动 CRM/邮件写入。

### Sprint H2 — Intelligence Runtime

- 复用现有 Workflow / AgentRun / Workers 构建 Research Planner 和专业执行阶段。
- 接入已授权 Research Tools；来源、限速、重试、成本与安全策略显式化。
- 完成 Evidence Validation、Entity Resolution 和结构化结果沉淀。
- 建立研究进度、失败、取消和恢复体验。

### Sprint H3 — Opportunity & Strategy

- 实现维度化机会评估、证据关联、冲突提示和可解释排序。
- 实现 Opportunity Board 与 Market Map。
- 加入跨行业测试夹具与评分校准数据。
- 资格确认由用户或可审计的业务规则完成。

### Sprint H4 — Trip Integration & Learning

- 用户确认后，把机会关联至现有 Customer / Place / Trip / Visit。
- 复用现有 Trip Builder、拜访简报、现场记录和会议纪要能力。
- 将真实 Visit Outcome / Trip Outcome 作为反馈来源。
- 仅在证据充分且标注来源后修订 Insight 与 Opportunity。
- 把有价值的结果写入现有 Memory 系统，并支持过期、纠错和版本替换。

---

## 11. 冻结原则

1. **通用性优先**：行业知识从用户输入与证据动态获得，不通过行业硬编码实现。
2. **复用现有领域**：Customer、Trip、Visit、Evidence、Workflow、AgentRun 和 Memory 是已有资产；先复用再扩展。
3. **证据优先**：报告语言不能替代来源；事实、推断、假设和未知必须区分。
4. **用户拥有正式数据决策权**：候选发现与正式业务主数据分离；关键写操作经既有授权机制。
5. **行动优先**：研究最终导向可执行商务行动，但不强迫每次 Trip 先做研究。
6. **不破坏现有差旅闭环**：任何市场智能缺陷或外部数据源故障不得阻断原有 Trip 核心能力。
7. **渐进式迁移**：迁移不可变、API 向前兼容、先加后删，必须有回滚与测试。
8. **先审代码再定表结构**：本文件冻结产品与逻辑架构，不代替对当前仓库实际 schema 的工程审计。
