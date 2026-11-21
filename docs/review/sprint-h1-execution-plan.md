# Sprint H1 — Domain Foundation & Evidence Data Architecture

> 状态：H1.0 源码审计已完成；H1.1 领域契约已冻结；H1.2 Workspace 与候选实体后端纵向切片已实现；H1.3 Evidence 版本化与 MarketInsight 已实现；H1.4 Opportunity 与 Workspace–Trip 关联已进入工程验证  
> 关联设计：`docs/design/market-intelligence-engine-v1.0.md`  
> 仓库基线：`main`，核对到的最新提交 `c5e98e2b567f54705a8569d0ef7ced138d7f88b2`  
> 计划日期：2026-11-21

---

## 1. H1 目标

H1 的目标不是上线一个会聊天的市场研究 Agent，而是先建立可长期演进的通用业务领域基础：

- 用户可以创建 Market Workspace，并输入任意行业/产品/服务的研究背景。
- 系统保存原始输入、可编辑的结构化上下文及字段来源。
- 研究问题、候选实体、市场判断和机会有明确的持久化关系。
- 市场证据复用现有 Evidence 能力，能够关联支持、反证和来源。
- 研究数据与正式 Customer / Trip / Visit 主数据边界清晰。
- 所有新对象具备组织隔离、审计、幂等和删除/撤销语义。
- 新能力不影响现有 Trip 创建、规划、地图、拜访、离线同步和差旅执行闭环。

H1 不实现完整外部研究抓取、行业策略生成或自主商务外联；这些属于 H2–H4。

---

## 2. 仓库现状与实施前置审计

以下信息来自本轮对 GitHub 仓库文件与最近提交的检查，不等同于本地执行完整测试。

### 2.1 已确认的现有资产

| 资产 | 当前仓库资料体现的能力 | H1 决策 |
|---|---|---|
| 后端 | Python + FastAPI，模块化单体、业务用例分层 | 沿用 |
| 客户端 | SwiftUI | 沿用 |
| 数据库 | PostgreSQL 16 + PostGIS + pgvector | 沿用 |
| API 契约 | OpenAPI 文件为契约基准 | 沿用 |
| Evidence | 已存在 Evidence 持久化能力，实施记录称迁移 0016 已建立并实际写入 | 先审字段，不新建重复主表 |
| Agent 工作流 | WorkflowInstance / AgentRun 已存在 | 复用其运行追踪 |
| LLM | Provider、调用审计、缓存、schema 校验、模板降级、工具目录均已有实现记录 | 复用 |
| Worker 基座 | Outbox、后台任务、租约、心跳、退避、DLQ 等已有实现记录 | 复用 |
| 组织隔离 | RLS、非 owner 应用角色和跨租户测试已有实现记录 | 新表必须继承 |
| 客户与出差 | Customers、Places、Trips、Visits、拜访记录、会议纪要、Trip Outcome 已存在 | 不重复建主对象 |
| 记忆 | 三层记忆、pgvector、混合检索、复盘 Worker 已有实现记录 | 复用反馈闭环 |
| 用户意图研究 | PR 基线已有 ResearchPlan、查询审阅、SearXNG 采集和未核验 Evidence | 作为单次 Research Workflow；由 Workspace 关联多个 Workflow，不重复建设 ResearchPlan 表/Worker |

### 2.2 迁移文档漂移（H1.0 已定位）

源码审计已确认 `0031_visa_catalog_overrides.py` 存在，revision 为 `0031_visa_catalog_overrides`，其父 revision 为 `0030_traveler_nationality`；模型注册入口也已导入 `VisaCatalogOverride`。README 的迁移目录说明曾停留在 0030，属于 README 落后，而不是迁移文件缺失。H1.0 审计报告：`docs/review/sprint-h1-code-audit.md`。

- 审计当时 README 已更新到 revision 0031；当前源码迁移清单已推进至 0034_market_insights_evidence_versions。
- H1.1 仍须在实际数据库环境执行 `alembic heads` 和 `alembic current`；源码 revision 图不能证明部署数据库已升级。
- 新迁移必须从 0031_visa_catalog_overrides 后向前新增，不得改写历史迁移。
- 若实际数据库发现多个 head 或版本落后，先按项目既有流程解决，不得绕过迁移治理。

### 2.3 代码审计必须覆盖

开始写迁移前，逐项核对实际代码：

1. Customer / Place / Trip / Visit 的 ORM、Pydantic schema、Repository、Service、UseCase、Route 和 OpenAPI。
2. Evidence 当前字段、引用方式、生命周期、组织隔离、删除行为，以及它目前是否仅用于 proactive suggestion 或已可支持通用来源证据。
3. WorkflowInstance / AgentRun 的输入输出 JSON、状态机、重试、取消、关联 Evidence 和审计字段。
4. Tool Registry 的权限表达、读写边界、工具执行是否强制经过业务 UseCase。
5. LLM Gateway 的结构化输出校验、prompt 版本、调用成本和失败降级。
6. Outbox / Background Jobs 的幂等键、租约、死信、恢复和消费者去重。
7. iOS 当前导航、Design System、网络层、离线缓存和 API decode 约定。
8. 所有组织级表的 RLS policy、FORCE ROW LEVEL SECURITY 和应用角色权限。

**审计交付物已完成**：`docs/review/sprint-h1-code-audit.md`，包含字段级复用矩阵、Evidence / Workflow / Customer / Trip / Visit 审查、迁移治理和发布安全发现。未读取源码或未执行的部分均明确标记为待核实。没有源码依据的推断不得进入迁移。

---

## 3. H1 逻辑数据模型

H1.1 领域契约已形成独立文件：`docs/architecture/market-intelligence-domain-contract-v1.0.md`。本执行计划中的对象清单为实施视图；字段、状态机、Evidence 版本语义、跨租户复合关系和 API 语义以该契约为准。若实现中发现与真实 ORM / PostgreSQL 约束不兼容，必须先记录 ADR 和兼容方案，不得静默偏离。



下面是首选逻辑拆分，不是未经审计即可执行的 DDL。实际实施必须优先检查现有表是否已能承载其中部分职责。

### 3.1 候选新对象

| 对象 | 关键职责 | 首选数据边界 |
|---|---|---|
| MarketWorkspace | 研究目标、创建人、组织、状态、当前上下文版本 | 组织级 |
| BusinessContextSnapshot | 原始输入、结构化结果、unknowns、字段 provenance、抽取版本 | 工作空间级、不可覆盖式版本 |
| ResearchQuestion | 研究问题、优先级、依赖、验收条件、状态与结果引用 | 工作空间级 |
| MarketEntity | 候选企业/人物/产品/机构/地点/项目等标准化实体 | 组织级候选实体，分类可扩展 |
| MarketEntityAlias | 别名、域名、语言变体、外部标识和来源 | 实体级 |
| MarketWorkspaceEntity | 实体进入某个 Workspace 的原因、相关性和研究状态 | 多对多关联 |
| MarketInsight | 判断文本、假设、未知、冲突状态和版本 | 工作空间级 |
| MarketOpportunity | 机会描述、评估维度、资格状态、证据完整度 | 工作空间级 |
| InsightEvidenceLink | Insight 与 Evidence 的支持/反驳关系 | 关联表 |
| OpportunityEvidenceLink | Opportunity 与 Evidence 的支持/反驳关系 | 关联表 |
| MarketEntityCustomerLink | 候选实体与正式 Customer 的匹配/确认关系 | 关联表 |
| MarketWorkspaceTripLink | Workspace 与现有 Trip 的关联 | 多对多关联 |

### 3.2 不应重复创建的对象

H1 不新建下列平行主对象：

- Evidence：先审计并复用当前 Evidence 表；若字段不够，只能向前兼容地扩展。
- AgentRun / WorkflowInstance：复用现有运行时。
- TargetAccount：由 MarketEntity + 用户确认的 Customer Link 表达。
- Mission：由现有 Trip 表达。
- VisitOutcome：复用 Visit Record、Meeting Minutes 和 Trip Outcome。
- BusinessProfile 全局画像：H1 先将上下文保存在 Workspace 的版本快照里；用户明确需要跨 Workspace 复用后，再设计可选的长期业务画像，不默认将一次研究输入变成账户级永久资料。

### 3.3 字段与约束原则

所有组织级实体至少需要：

- 主键、organization_id、创建人和更新时间；
- 明确的状态字段与服务端状态迁移校验；
- 需要审计的操作记录；
- 按实际查询模式设计索引；
- RLS 与数据库级租户隔离；
- 幂等和重复请求语义；
- 删除、撤销、归档和保留策略。

上下文版本必须同时保存：
- 原始用户输入；
- 模型抽取结果；
- 用户修改后的值；
- 每个字段的来源/置信度；
- 使用的 schema 和 prompt 版本。

原始输入不得被后续模型重跑覆盖。重新抽取应生成新版本，并通过明确操作采纳。

### 3.4 动态行业与实体类型

- 不以数据库 ENUM 或 Python Literal 固定行业清单。
- industry、customer_segment、business_model、entity_kind 使用可演进的分类值或受控 taxonomy key。
- 保留用户原始标签；标准标签可以合并、映射和多语言显示。
- 允许未知标签和行业专属 JSONB 扩展，但 JSONB 不得成为无约束的主数据垃圾桶。
- 对分类词表建立版本和来源；模型可以建议分类，用户可修订。

### 3.5 实体解析与正式客户映射

- 先尝试可靠外部 ID、域名、地址/国家和名称组合，再做模糊匹配。
- 名称相同不能单独作为自动合并依据。
- 低置信度匹配只产生候选关系。
- 发生冲突时保留多个候选与解释，不进行破坏性覆盖。
- 创建 Customer 或关联现有 Customer 必须走既有业务用例和权限校验。
- 通过唯一约束、幂等键和并发测试阻止重复客户。

---

## 4. API 与应用用例边界

最终路由和 schema 名称以仓库既有 API 风格为准。建议的能力边界：

| 能力 | 操作 | 规则 |
|---|---|---|
| Workspace | 创建、列表、详情、归档 | 创建保留 raw_input；所有访问校验 org scope |
| Context | 查看、编辑、重新抽取、采纳版本 | 模型重抽取不覆盖用户修改 |
| Research Questions | 创建、编辑、排序、完成/阻塞 | 研究完成必须可追溯结果或阻塞原因 |
| Entities | 候选列表、详情、合并候选、关联 Customer | 不自动写入 Customer 主数据 |
| Insights | 列表、详情、证据展开、接受/驳回/过期 | 每个重要判断可追溯来源 |
| Opportunities | 列表、详情、维度评估、资格确认 | 资格状态有规则和操作者 |
| Trip links | 关联/解除关联现有 Trip | 不复制 Trip 数据 |

实施要求：

- Route 只做 HTTP 语义、身份上下文和输入输出，不承载业务判断。
- 业务规则进入现有 Domain / Application UseCase 层。
- Repository 统一处理组织过滤和事务边界，但不能替代数据库 RLS。
- 所有写操作按项目现有标准支持 Idempotency-Key。
- 错误码与响应模型沿用现有 API 约定。
- 新端点更新 OpenAPI，并补齐契约漂移测试与 Swift 解码样本。
- 允许分页、过滤、排序；禁止一次性返回整个 Workspace 的全部 Evidence 原文。

---

## 5. 前端 H1 最小但完整的交付

H1 不追求完整市场研究视觉效果，但必须形成可理解、可操作的基础体验。

### 5.1 核心路径

1. 用户从市场研究入口创建 Workspace，或从 Trip 详情进入并关联 Workspace。
2. 用户输入自然语言背景与研究目标。
3. Context Review 展示原始输入、AI 抽取字段、未知项和用户修订入口。
4. 用户确认上下文后保存为版本。
5. 用户可以查看研究问题列表、添加问题、调整优先级。
6. Workspace Overview 显示状态、信息缺口和后续阶段入口。
7. 用户可以查看已有的 Evidence / Insight / Opportunity 结构记录（即使 H1 尚未接完整外部 Research Agent，也不得用假资料伪装为真实结果）。

### 5.2 体验要求

- 复用现有 SwiftUI Design System：颜色、间距、字体、圆角、徽标和数据质量标签。
- 保持现有根导航与 Trip 详情结构，不制造重复入口。
- 表单采用渐进披露，不一次展示大量空字段。
- AI 抽取字段必须能辨别“用户原文 / AI 建议 / 用户已确认”。
- 明确区分空数据、加载中、失败、未接入数据源和暂无证据。
- 长任务进度来自后端真实状态；客户端关闭再打开后仍能恢复显示。
- 不使用 emoji 代替产品图标，不用长篇 AI 文案代替结构化信息。

---

## 6. H1 测试计划

### 6.1 后端单元测试

- BusinessContext 原始输入保留；
- 重新抽取生成新版本；
- 用户修改字段不会被 Agent 重跑覆盖；
- 动态行业/客户类别不会被枚举限制；
- 未知字段保持 Unknown；
- 状态迁移遵守允许的转换；
- Evidence 与 Insight / Opportunity 的支持、反驳关系正确；
- Workspace / Entity / Opportunity 的组织范围校验正确；
- Entity 与 Customer 的链接不引入重复客户；
- 幂等键重复提交返回首次结果；
- 同一实体并发创建与链接不存在竞态重复。

### 6.2 PostgreSQL 集成测试

- 新增组织级表全部启用并强制 RLS；
- 组织 A 无法读取或修改组织 B 的 Workspace、Entity、Insight、Opportunity；
- 连接池复用不会造成组织上下文串用；
- 事务回滚时不留下孤立 Evidence Link；
- 外键、唯一约束、删除保护和级联策略符合领域语义；
- 数据库中无法通过直接 SQL 写入非法状态（必要约束由 DB 与服务共同保证）。

### 6.3 API / OpenAPI / iOS 契约测试

- 新 API 成功、权限拒绝、跨租户 404/拒绝、重复请求、非法状态和分页样本；
- OpenAPI 导出与仓库契约文件无漂移；
- Swift Decodable 样本覆盖新增可选字段、Unknown 和状态枚举；
- 离线/网络失败时不把未同步修改伪装成已保存；
- 空数据和加载态有 UI 冒烟覆盖。

### 6.4 跨行业泛化测试

使用五份独立测试输入（工业设备、企业软件、医疗器械、商业咨询、消费品），并增加：
- 只输入一句话；
- 不提供行业；
- 同时覆盖多个行业；
- 用户目标市场中途修改；
- 用户否决某个 AI 推断；
- 多来源证据互相矛盾。

H1 主要验收上下文提取和领域持久化结构；真正的证据发现质量和 Research Planner 泛化属于 H2 验收。

### 6.5 回归门禁

必须保持现有项目门禁，包括：
- make test
- make typecheck
- make lint / make check
- make contract-check
- make e2e
- make ios-build
- make ios-test
- make ci

实际命令以当前 Makefile 为准；任何未执行的门禁必须在交付记录中明确标为未执行，不得宣称全绿。

---

## 7. 实施顺序与交付门槛

### H1.0 — 代码与 schema 审计

交付：
- 当前代码结构和真实迁移 head；
- Customer / Evidence / AgentRun / Workflow / Trip / Visit 字段级复用矩阵；
- 已有 API、服务、Worker 和 iOS 模型映射；
- 迁移、RLS、审计与数据删除影响分析。

**Exit Gate**：不再存在“可能已有/可能没有”的关键数据模型假设。

### H1.1 — 领域契约冻结

交付：
- 领域对象与关系图；
- Pydantic 请求/响应 schema；
- 状态机与状态迁移表；
- 组织范围、索引、幂等和审计清单；
- API 与 OpenAPI 变更提案。

**Exit Gate**：Customer / Evidence / Trip 没有重复主对象，所有新增字段和关系都有明确 owner。

### H1.2 — 数据库与后端基础

交付：
- Alembic 向前兼容迁移；
- ORM / Repository / Domain Service / Application UseCase；
- 新表 RLS、索引、约束与审计；
- Workspace / Context / ResearchQuestion / Entity 的基础 API；
- Evidence 关联层的兼容实现。

**Exit Gate**：真实 PostgreSQL 集成测试和跨租户测试通过；迁移可升级并有恢复方案。

### H1.3 — Opportunity 基础与 Trip 关联

交付：
- Insight / Opportunity / Evidence Link；
- Entity 与 Customer 的用户确认链接；
- Workspace 与 Trip 的关联；
- 机会资格状态服务端校验；
- 幂等和并发测试。

**Exit Gate**：不存在重复 Customer；候选机会不会未经确认变成正式客户或已成交商机。

### H1.4 — SwiftUI 基础工作区

交付：
- Workspace 列表/创建/详情；
- Context Review；
- Research Questions；
- 基础 Insight / Opportunity 详情结构；
- 设计系统一致、加载/空/失败/离线状态完整。

**Exit Gate**：真机或模拟器连接真实后端完成创建、修改、关闭再打开恢复和跨租户权限验证。

### H1.5 — Research Worker 版本化证据集成

交付：
- 上述测试全部执行记录；
- API 契约与 iOS 模型样本更新；
- README / 实施进度 / 迁移清单同步；
- H1 变更说明与未完成项清单。

**Exit Gate**：现有 Trip 核心验收无回归；未执行的测试和未接入的数据源显式列出。

---

## 8. H1 明确不做的事

- 不开发任意网页爬虫或无边界 Browser Agent。
- 不在 H1 把所有 Agent 做成独立微服务。
- 不为展示效果预置虚构客户、证据、评分或“市场热度”。
- 不把市场研究强制加入 Trip 创建流程。
- 不自动向 Customer 主数据写入 AI 候选。
- 不自动发邮件、询价、预约或预订。
- 不一次性重写现有 Trip / Customer / Visit / Evidence。
- 不以 API 已返回 200、静态 UI 可展示或单元测试通过代替真实端到端验收。

---

## 9. 当前交付状态说明

本计划已经从设计阶段进入 H1.2 工程实现。源码审计结果见 `docs/review/sprint-h1-code-audit.md`；领域契约见 `docs/architecture/market-intelligence-domain-contract-v1.0.md`。当前分支中的实际代码、迁移与测试以 PR #3 为准，不再沿用早期“只有设计文档、没有实现代码”的历史描述。

**当前已实现：**
- Workspace、版本化 ContextSnapshot、ResearchQuestion、活动事件及 Workspace ↔ Workflow 关系；
- Workspace 对现有用户意图 ResearchPlan 的关联与查询；
- MarketEntity / Alias / WorkspaceEntity / EntityCustomerLink，含显式复用与 Customer 确认边界；
- Alembic 0032 / 0033 / 0034、FORCE RLS、同组织复合外键、API 和集成测试；
- OpenAPI 与 Swift 契约样本由项目生成器同步。

**当前尚未完成：**
- 原生 iOS Workspace / Opportunity / Insight 工作台；自动研究与证据验证 Agent；
- 原生 iOS Workspace 列表/详情/Context Review；
- 真实数据库迁移与完整门禁最终验证。

**验证纪律：** H1.2 / PR #3 的严格 CI #210 已验证 Backend 与 iOS 全部通过（含 PostgreSQL 迁移、Alembic drift、全量 Backend pytest、SwiftLint、Xcode Build 与 iOS Unit Tests）。H1.3 / PR #4 已新增 Evidence 与 Insight 切片；其当前严格 CI 仍在执行，只有最新 PR #4 运行全部通过后，才能将 0034 迁移、Alembic drift、全量测试和 iOS 验证标记为通过。

## 10. H1.2 纵向切片（叠加现有用户驱动 ResearchPlan）

### 10.1 产品边界

- MarketWorkspace 是长期研究空间；可容纳多个 ResearchPlan、研究问题及后续 MarketEntity / Insight / Opportunity。
- 现有 ResearchPlan 是一次具体研究执行计划，继续以 WorkflowInstance（workflow_type="research_plan"）为唯一运行实体。
- 现有外部检索仍由用户确认查询后，复用既有 BackgroundJob、SearXNG Provider、AgentRun 和 Evidence 执行。
- Workspace 与 ResearchPlan 通过 MarketWorkspaceWorkflow 关联；独立 ResearchPlan 仍可不关联 Workspace。
- MarketWorkspace 不强迫绑定 Trip；ResearchPlan 可根据 source_scope 选择读取内部 Trip/Customer 摘要，也可完全 external-only。
- 不新建 ResearchPlan 表，不新建第二套搜索 Worker 或外部检索适配器。

### 10.2 当前分支实现

- 分支：feat/sprint-h1.2-market-workspace-on-research-plan（基于 ui-foundation-2026-11）。
- Draft PR：#3 https://github.com/kerrysmith134-sketch/global-biztravel/pull/3；之前从 main 创建的 PR #2 已关闭并标记为被 PR #3 替代，未合并任何代码。
- 新增 ORM：MarketWorkspace、MarketContextSnapshot、MarketResearchQuestion、MarketQuestionDependency、MarketActivityEvent、MarketWorkspaceWorkflow、MarketEntity、MarketEntityAlias、MarketWorkspaceEntity、MarketEntityCustomerLink。
- 新增迁移：0032_market_workspace_foundation 与 0033_market_entities，从 0031_visa_catalog_overrides 顺序向前；WorkflowInstance、Customer、Evidence 增加 (organization_id,id) 复合唯一约束，供新关系复合外键强制同租户关联；全部新增组织表 FORCE RLS。
- 新增 API：Workspace、Context 快照、ResearchQuestion、活动事件、候选实体列表/创建、候选实体与 Customer 的匹配建议/人工确认，以及关联 ResearchPlan 列表；实体列表分页并批量加载别名，避免 N+1。
- Research API 增加可选 workspace_id；只有同组织 Workspace 存在时才创建关联；外部研究依旧由现有用户确认查询工作流执行。
- iOS ResearchPlan 请求模型与 ResearchWorkspaceView 支持可选 workspaceId；默认 nil 保持旧调用兼容。
- 集成测试新增 Workspace 生命周期、上下文版本采纳、研究问题父子关系、幂等、活动事件、Workspace ↔ ResearchPlan 关联、Entity 候选发现/域名重复提示/显式复用/Customer 确认、跨租户 API/RLS 和数据库复合外键负向场景。

### 10.3 尚未完成 / 不得宣称完成

- MarketInsight、MarketOpportunity、Evidence 内容版本化及其支持/反驳关联仍未实现；MarketEntity 候选实体与 Customer 确认关系已实现。
- H1.3 Workspace 原生列表/详情/Context Review 的完整 iOS 界面尚未实现；当前 iOS 只支持把 ResearchPlan 请求可选关联到已知 Workspace ID。
- OpenAPI YAML/JSON 需由项目流程重新导出并执行 make contract-check。
- 本轮未在此执行环境本地 checkout 或直接运行数据库命令；GitHub Actions 已验证 Ruff、MyPy、OpenAPI 生成与 contract-check（107 paths / 182 schemas）。CI #196 的 docs-check 因迁移清单未更新而失败，当前已登记 0032/0033；新一轮严格 CI 仍需执行 docs-check、PostgreSQL migrate、Alembic check、全量测试与 iOS build/test，不能提前宣称全绿。

### 10.4 合并顺序

1. 先审查并验证 ui-foundation-2026-11 上已有用户驱动 ResearchPlan PR。
2. 该基线合并后，再合并本 H1.2 叠加 PR。
3. 若 ResearchPlan 设计发生变化，必须同步检查 MarketWorkspaceWorkflow、workspace_id API 兼容和测试。


## 11. H1.3 Evidence 与 Insight 纵向切片

新增迁移 `0034_market_insights_evidence_versions`，扩展现有 Evidence，而不是另建证据主表；所有新增 Evidence 元数据均允许 NULL，旧证据调用方继续使用原引用语义与原唯一约束。市场来源证据采用 `market-source:v1:<sha256>` 作为版本 ref；版本 identity 同时包含规范化来源地址和原文内容哈希。相同来源、相同原文可幂等返回；来源内容变化生成新 Evidence 行；不同来源即使内容相同也保留为不同记录。

MarketInsight 采用 `workspace_id + insight_key + version` 建立版本链；历史判断不提供原位编辑接口。支持、反驳和上下文证据通过 MarketInsightEvidence 明确连接。AI 创建的 Insight 默认 NEEDS_REVIEW；同时具有 SUPPORTS 和 CONTRADICTS 时标记 CONFLICTED。人工采纳必须有支持证据且不存在未解决反驳，并记录操作者、时间和说明。同一 insight_key 通过部分唯一索引最多只能有一个 ACCEPTED 版本。没有证据的判断只能以显式证据缺口创建，不能被采纳。

该切片暂不包括 MarketOpportunity、OpportunityEvidence、Trip 关联、自动证据发现/核验 Agent 和 iOS 原生 Insight UI；不能将 API 持久化等同于完整 Agentic 市场研究闭环。


## 12. H1.4 Opportunity 与 Trip 纵向切片

新增迁移文件 `0035_market_opportunities.py`（revision `0035_market_opps`，父 revision `0034_market_insights`），实现：
- MarketOpportunity：Workspace 级候选机会与受控状态机；
- MarketOpportunityAssessment：追加式、带版本的评估快照；Opportunity 主记录仅保留当前评估投影；
- MarketOpportunityEvidence：显式支持/反驳/上下文证据关系，且只允许引用 Workspace 或关联 ResearchPlan 的 Evidence；
- MarketOpportunityEntity：Opportunity 只能关联当前 Workspace 已关联的 MarketEntity；
- MarketWorkspaceTripLink：Workspace ↔ 现有 Trip 多对多；解除是软状态并写审计，不会级联删除 Trip；
- trips 增加 (organization_id,id) 唯一键，以数据库复合外键保证 Workspace Trip Link 的租户安全。

状态机不接受任意 PATCH：
- DISCOVERED → RESEARCHING → EVIDENCE_READY → QUALIFICATION_PENDING；
- QUALIFIED 必须通过 Admin 人工动作，具备至少一条 SUPPORTS，且没有未解决 CONTRADICTS；
- ACTION_PLANNED 只能从 QUALIFIED 推进；
- WON / LOST 必须从 ACTION_PLANNED 记录，并提供实际业务结果说明；
- DISQUALIFIED 必须提供原因；ON_HOLD 的恢复路径由服务端白名单管理。

评估维度采用开放 JSON 对象；每个维度至少有 value、rationale，允许 UNKNOWN。引用证据必须已关联到当前 Opportunity；未知值不会被当作 0，也不会生成成交概率分数。评估每次追加新版本，不覆盖历史。

**验证状态：** 最新完成的 H1.4 严格 CI 已通过 Ruff、MyPy、OpenAPI / iOS contract、docs、0035 PostgreSQL migration、Alembic drift 与 Backend pytest（416 passed / 1 skipped）；SwiftLint 与 Xcode Build 通过。为补强集成测试的多次运行清理和 Workspace 删除语义，已追加一项测试修正，最终 iOS Unit Tests 与全量 CI 将在该最新 head 再次确认后方可关闭 PR。原生 iOS Opportunity UI 和 Research/Evidence 自动化仍在后续阶段。


## 13. H1.5 Research Worker → Evidence Graph

现有 `research_execute` Worker 在 Workspace 绑定的场景下不再调用旧 `record_many()` 保存原始 URL ref。它改为：
1. 从 ResearchPlan Workflow 找到已关联的 Workspace；
2. 对每个搜索结果保守规范化来源 URL，对标题 + 搜索摘要原文计算 SHA-256；
3. 复用现有 `Evidence` 表，使用 `market-source:v1:<source+content hash>` 作为不可变版本 ref；
4. 通过新增 `MarketResearchPlanEvidence` 关联表记录 ResearchPlan、Evidence、采集 AgentRun 与每条用户已审阅 query；
5. 同 URL 同内容跨不同 ResearchPlan 只共享一条 Workspace Evidence；内容变化新增 Evidence 版本，不覆盖旧行；
6. ResearchPlan 详情将自身旧 Evidence 与新关联的 Workspace Evidence 合并返回；未绑定 Workspace 的独立 ResearchPlan 继续走兼容路径。

迁移 `0036_market_plan_ev` 对 Workflow 关联、Evidence 与 AgentRun 全部使用同组织复合外键并启用 FORCE RLS。新增集成测试验证共享去重、查询归因、内容变化版本、详情读取和活动事件幂等。

**验证纪律：** 该分支需要对最新 H1.5 head 重新执行 Ruff、MyPy、文档/契约检查、0036 PostgreSQL migration、Alembic drift、全量 Backend pytest 与 iOS Build/Unit Tests。此时不宣称该新切片已通过 CI。


## 14. H1.6 ResearchQuestion → Evidence-backed Answer

问题不再只有 OPEN/IN_PROGRESS/ANSWERED/BLOCKED 状态及一个覆盖式 answer_summary。新增 MarketResearchQuestionAnswer 版本记录和 MarketResearchQuestionAnswerEvidence 显式证据关系。

一次回答从 NEEDS_REVIEW 开始；存在支持与反驳时标记 CONFLICTED。只有 Admin 审核采纳后，Question 才更新为 ANSWERED。采纳必须有 SUPPORTS 且无 CONTRADICTS；independent_sources minimum 通过不同来源 host 数量校验；其他无法自动核验的 acceptance criteria 要求 review_note。修订答案追加版本并保留 supersedes 关系，同一 Question 只允许一个 ACCEPTED 版本。

本切片提供答案版本创建/查询、采纳、驳回 API；它为未来 Research Answer Agent 提供持久化边界，但当前并未声称 LLM 自动综合、全文抓取或事实核验 Agent 已经完成。

验证：最新 CI 对该分支完整树运行后，方可确认 0037 迁移、Alembic drift、Backend 集成测试及 iOS 回归全部通过。


## 15. H1.7 Research Answer Agent — asynchronous draft loop

H1.7 把 H1.6 的版本化问题答案变成真正的 Agent 执行链：
1. 用户在 Workspace 对一个问题显式启动答案草拟；
2. API 读取问题与当前可用 Evidence，形成最多 24 条的固定快照；用户可传入 evidence_ids 限定来源；
3. 使用 Workspace 锁、证据快照哈希和现有 BackgroundJob dedupe，避免同一问题/同一证据快照重复排队；
4. 通过现有 WorkflowInstance + MarketWorkspaceWorkflow 表达任务归属；不创建新的 Workflow 主表、Worker 服务或队列；
5. 现有 TASK_HANDLERS Worker 创建 AgentRun，通过唯一 LLMGateway 读取通用研究问题 Prompt，模型只能基于输入 Evidence ID 形成 SUPPORTS / CONTRADICTS / CONTEXT 判断；
6. 服务端丢弃模型虚构的 Evidence ID；未 VERIFIED 来源不允许 HIGH confidence；无 SUPPORTS 映射时强制 INSUFFICIENT；
7. 将答案追加为 MarketResearchQuestionAnswer 版本，保留 assumptions、unknowns、confidence、evidence_gap、Evidence 关系、AgentRun 和 LLMInvocation；
8. Workflow COMPLETED 仅表示草稿完成。答案仍待审核，Question 维持原状态；Admin 才能通过 H1.6 审核接口采纳。

无 Evidence 或 Gateway 降级时生成明确的 INSUFFICIENT 草稿，而不是用模板伪装成功结论。外部资料的全文抓取和 Verification Agent 不在此切片内。

**验证纪律：** PR #7 的 H1.6 修复断言已提交；PR #6 已严格 CI 全绿。H1.7 需独立通过 Ruff、MyPy、OpenAPI/docs、PostgreSQL migration/Alembic drift（虽无新迁移仍执行完整检查）、全量 Backend pytest、iOS SwiftLint/Build/Unit Tests 后再标记完成。


## 16. H1.8 用户选定来源网页提取（叠加 H1.7）

- 分支：`feat/sprint-h1.8-source-page-extraction`；基于 `feat/sprint-h1.7-research-answer-agent`，不从 main 重开重复基础分支。
- 新增安全 Provider：只允许用户从 Workspace 选定一条 `WEB_SEARCH_RESULT` 后触发；搜索结果列表不会自动抓取。
- SSRF 防护：标准 80/443 端口、拒绝 URL credentials、DNS 全地址公网校验 + IP pinning、每次重定向重新检查、HTTPS 禁止降级；响应字节、类型、超时和重定向次数均有边界。
- 新增迁移 `0038_market_evidence_derivations`：追加式来源到提取版本 lineage，组织/Workspace/Workflow/AgentRun/Evidence 复合外键，FORCE RLS 与只追加数据库权限。
- 复用现有 BackgroundJob/WorkflowInstance/AgentRun，不另建队列或 Agent Runtime；抓取失败进入既有重试/DLQ。
- 内容只存有上限摘录与规范化全文 hash；输出始终 UNVERIFIED，独立事实核验留给 H1.9。
- Draft PR 及严格 CI 尚待创建/执行；当前不声称迁移、RLS、Alembic drift、pytest 或 iOS 门禁已通过。


## 17. H1.9 Claim-level Verification Agent

- 分支：`feat/sprint-h1.9-claim-verification-agent`，直接叠加 H1.8，保留既有 PR 栈。
- 用户显式选定某一答案版本后触发；不自动抓取、不自动扩展证据集合。
- LLM 把答案拆成原子主张并为每条主张映射 SUPPORTS / CONTRADICTS / CONTEXT；服务端校验 Evidence ID，重算冲突与暂定结论。
- 新增 `MarketClaimVerificationRun`、`MarketClaim`、`MarketClaimEvidence`；迁移 `0039_market_claim_verification`；复用现有队列、Workflow、AgentRun 和 LLM Gateway。
- 不将不同 host 视为来源独立；数据库固定 `confirmed_independent_source_count=0`；Agent 不会产生 VERIFIED，也不会输出 HIGH confidence。
- 管理员只可人工采纳无反驳且有支持关系的暂定主张；人工采纳不等于事实认证，也不更改原答案版本。
- 严格 CI 尚待执行，尤其 Alembic metadata drift、PostgreSQL RLS 与全量 pytest。
