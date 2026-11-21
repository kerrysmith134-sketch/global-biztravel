# Sprint H1 — Domain Foundation & Evidence Data Architecture

> 状态：H1.0 源码审计已完成；H1.1 领域契约已提交；H1.2 首批后端基础切片已实现（待 CI / PostgreSQL 验证）  
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

### 2.2 迁移文档漂移（H1.0 已定位）

源码审计已确认 `0031_visa_catalog_overrides.py` 存在，revision 为 `0031_visa_catalog_overrides`，其父 revision 为 `0030_traveler_nationality`；模型注册入口也已导入 `VisaCatalogOverride`。README 的迁移目录说明曾停留在 0030，属于 README 落后，而不是迁移文件缺失。H1.0 审计报告：`docs/review/sprint-h1-code-audit.md`。

- README 已更新为源码最新 revision 0031。
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

### H1.5 — 回归与交付

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

H1.0 审计和 H1.1 领域契约已提交。H1.2 当前分支已实施首个可独立验收的后端纵向切片：

- 新增 `MarketWorkspace`、`MarketContextSnapshot`、`MarketResearchQuestion`、`MarketActivityEvent` ORM 与迁移 `0032_market_workspace_foundation`；
- 新增 Workspace、上下文版本、研究问题和活动时间线 API；
- 新增真实 PostgreSQL 集成测试文件，覆盖租户隔离、原始输入、上下文采纳、幂等和问题关系；
- 复用现有会员权限、RLS、幂等、错误 envelope，不增加第二套 Agent Runtime / CRM。

**这不是 H1.2 或 H1 完成声明。** 本分支尚待 GitHub Actions 执行 lint、typecheck、OpenAPI / iOS 契约检查、Alembic migration / metadata check 和真实 PostgreSQL 集成测试；当前不能声称迁移已在任何部署数据库执行或测试已通过。MarketEntity、Insight、Opportunity、Evidence 扩展/关联、完整研究 Worker 与 iOS 工作区仍待实施。
