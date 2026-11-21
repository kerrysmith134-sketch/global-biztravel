# Market Intelligence Domain Contract v1.0
> 状态：Sprint H1.1 领域契约冻结候选  
> 依据：Market Intelligence Engine v1.0 与 Sprint H1.0 源码审计  
> 日期：2026-11-21  
> 适用：通用 Global Business Travel OS，不绑定行业、公司或产品

---

## 1. 核心决策

本文件把市场情报领域从概念图推进到可实施的数据契约，冻结逻辑实体、状态机、关系边界、证据版本化、租户隔离和 API 语义。

**扩展现有系统，不另起平行 CRM、Mission、Evidence 或 Agent Runtime。**

| 领域职责 | 唯一真相源 |
|---|---|
| 出差 | 现有 Trip |
| 客户 | 现有 Customer |
| 地址/坐标 | 现有 Place / CustomerPlace |
| 实际拜访 | 现有 Visit / VisitRecord / Meeting Minutes |
| Agent 工作流 | 现有 WorkflowInstance / AgentRun |
| 长任务 | 现有 BackgroundJob / Worker |
| 模型调用 | 现有 LLMInvocation / LLM Gateway |
| 通用证据存储 | 现有 Evidence（兼容扩展） |
| 单次研究计划/执行 | 现有 WorkflowInstance（workflow_type=research_plan）+ AgentRun + BackgroundJob |
| 市场探索工作空间 | 新增 MarketWorkspace |
| 市场候选实体 | 新增 MarketEntity |
| 市场判断 | 新增 MarketInsight |
| 商业机会 | 新增 MarketOpportunity |

TravelerProfile 只表达旅客偏好与旅行背景，不承载企业业务画像。H1 不建立全局 BusinessProfile；业务上下文按 Workspace 版本快照保存，避免不同产品线和不同市场的业务背景被错误合并。

---

## 2. 组织级数据与通用约束

### 2.1 组织级表基线

除明确注明外，每张新表都必须包含：
- id：UUID 主键；
- organization_id：非空组织 ID；
- created_at：timestamptz；
- updated_at：仅可变实体需要；追加式历史表无需；
- created_by_user_id：可空用户外键，系统任务创建时允许为空。

每张组织级表必须在创建同一迁移中启用 RLS ENABLE + FORCE，并使用 migrations.helpers.enable_org_rls(table, force=True)。应用层仍需显式组织过滤，不能把 RLS 当作省略业务校验的理由。

### 2.2 跨租户关系强约束

仅在 API 层检查 organization_id 不足以防止错误关系写入。

对新领域实体与现有 Customer、Trip、Place、WorkflowInstance、Evidence 的跨表关联，实施时必须确保关联双方属于同一 organization。采用包含 organization_id 的复合唯一键/复合外键；如现有表缺少所需的 (organization_id, id) 唯一约束，可在向前兼容迁移中增加冗余唯一约束，不改变现有主键及调用语义。

禁止仅凭 UUID 目标记录存在就建立跨租户关系。

### 2.3 动态分类

以下值必须为可扩展文本或 taxonomy key，不得由 SQL CHECK、Python Literal 或 Swift enum 限定为固定行业清单：
- industry
- entity_kind
- business_model
- customer_segment
- offering_kind
- insight_type
- source_type

稳定的工作流状态和权限状态可以使用受控状态集合。业务分类不可与工作流状态混为一谈。

### 2.4 原始输入与 AI 推断

用户原始输入必须不可变保存。AI 提取结果、用户覆盖值、unknowns、provenance 分开存储。重新抽取生成新版本，不能覆盖旧输入或用户修改。未知字段保持空/Unknown，不填入默认行业、客户类型或市场事实。

---

## 3. 核心实体契约

### 3.1 MarketWorkspace

用途：一次有边界的市场探索；可以独立于 Trip 存在，也可以关联多个 Trip。

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| name | text | 必填，用户可编辑 |
| objective | text | 本次希望回答/达成的业务目标 |
| status | text | DRAFT / ACTIVE / PAUSED / COMPLETED / ARCHIVED |
| owner_user_id | UUID nullable | 工作空间负责人 |
| current_context_version | integer nullable | 当前采纳上下文版本；无上下文时为空 |
| settings | JSONB | 用户可配置偏好；不存储凭证 |

约束：
- Workspace 仅属于一个 organization。
- 名称不是唯一键；允许同一组织有多个相似研究。
- ARCHIVED 不等于硬删除。
- 一个 Workspace 可关联多个 WorkflowInstance、Trip 和 MarketEntity。
- 没有 Workspace 的 Trip 流程必须完全可用。

### 3.2 MarketContextSnapshot

用途：保存某次研究的原始业务上下文和每次提取/人工修订的版本。

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| workspace_id | UUID | 所属 Workspace |
| version | integer | 从 1 开始，workspace 内递增 |
| raw_input | text | 用户原文，必填且不可由模型覆盖 |
| extracted_context | JSONB | 模型提取的结构化字段 |
| user_overrides | JSONB | 用户逐字段修改/确认 |
| unknowns | JSONB | 仍缺少、不能推断的信息 |
| provenance | JSONB | 每个字段来源：USER / USER_EDIT / MODEL / EVIDENCE |
| extraction_status | text | NOT_STARTED / PENDING / COMPLETED / FAILED |
| extraction_prompt_id | text nullable | prompt 模板标识 |
| extraction_prompt_version | text nullable | prompt 版本 |
| is_current | boolean | 当前采纳版本标志 |
| created_by_user_id | UUID nullable | 人工发起时记录 |

约束：
- UNIQUE (organization_id, workspace_id, version)。
- 一个 Workspace 同时最多一个 is_current = true（partial unique index）。
- 历史版本不可覆写；更改时新增 version。
- 模型失败不能阻止保存 raw_input；提取失败明确呈现。
- 已确认字段必须保留 user override 与来源，不允许重跑模型覆盖用户决定。

建议 extracted_context 顶层结构（键开放，不固定行业字段枚举）：

~~~json
{
  "offerings": [],
  "business_models": [],
  "industries": [],
  "customer_segments": [],
  "value_propositions": [],
  "target_markets": [],
  "goals": [],
  "constraints": [],
  "assumptions": [],
  "extensions": {}
}
~~~

数组项应保留原始 label、规范化 label（可空）、description、provenance 和 confidence。未识别项进入 unknowns，不丢弃原文。

### 3.3 MarketResearchQuestion

用途：用户提出或 Planner 动态拆解出的可验证问题。

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| workspace_id | UUID | 所属 Workspace |
| parent_question_id | UUID nullable | 子问题父级 |
| question | text | 明确、可回答的问题 |
| rationale | text nullable | 为什么需要研究 |
| source | text | USER / AGENT |
| priority | smallint | 1–5，1 最高 |
| status | text | OPEN / IN_PROGRESS / ANSWERED / BLOCKED / CANCELLED |
| acceptance_criteria | JSONB | 完成门槛/预期证据 |
| answer_summary | text nullable | 结论摘要，必须关联证据或明确缺口 |
| blocked_reason | text nullable | 阻塞原因 |

约束：
- 问题必须属于一个 Workspace。
- parent 必须属于同 Workspace。
- 依赖关系通过独立 MarketQuestionDependency 表表达；同 Workspace，禁止自依赖和重复边。
- 依赖图必须在 Service 层阻止环路，不能只依赖 UI。
- ANSWERED 必须有 answer_summary 且关联 Evidence/Insight，或明确说明未找到足够证据。
- AGENT 创建的问题必须记录对应 workflow/run 的来源。

### 3.4 MarketEntity

用途：研究中发现的公司、机构、人物、产品、地点、项目、渠道或其他候选实体。它不是 Customer 主数据。

| 字段 | 类型 | 约束/说明 |
|---|---|---|
| entity_kind | text | 动态分类，非固定枚举 |
| display_name | text | 当前显示名称 |
| normalized_name | text | 用于匹配的标准化文本 |
| description | text nullable | 研究摘要 |
| canonical_domain | text nullable | 来源中观察到的主域名；同域名不等于同一法律/商业主体 |
| country_code | char(2) nullable | 可为空；不得猜测 |
| attributes | JSONB | 有 schema version 的扩展属性 |
| status | text | CANDIDATE / REVIEWED / VERIFIED / REJECTED / MERGED |
| merged_into_entity_id | UUID nullable | 合并目标，需同组织 |
| created_by_run_id | UUID nullable | 来源 AgentRun |

约束：
- 原始名称和别名不得因规范化而丢失。
- 不以名称相似度、国家或域名单独自动合并；域名相同（如母子公司、品牌站、区域站）只产生 possible-duplicate 提示。
- 跨 Workspace 复用一个 MarketEntity 必须由用户显式选择已有 entity_id；不能通过 canonical_domain 隐式复用。
- MERGED 必须指向同组织、非自身的目标实体，并由用户/授权业务规则显式确认；原实体仍可审计。
- VERIFIED 需要证据或用户确认，不由模型自评触发。

### 3.5 MarketEntityAlias

用途：保存名称变体、翻译名、域名、外部标识及其来源。

字段：entity_id、alias_type（动态文本）、raw_value、normalized_value、source_evidence_id（nullable）、created_at。

同一个 alias（包括 canonical domain）可以对应多个候选实体，因此只在同一实体内约束重复 alias；不得设置 organization + alias_type + normalized_value 全局唯一。应建立检索索引并返回歧义候选。

### 3.6 MarketWorkspaceEntity

用途：表达某候选实体为何进入某个 Workspace，以及研究状态。

字段：workspace_id、entity_id、discovery_reason、relevance_note、status、first_seen_at、last_seen_at。

约束：UNIQUE (organization_id, workspace_id, entity_id)。同一实体可以出现在多个 Workspace。

### 3.7 MarketInsight

用途：保存可追溯、可修订的市场判断。Meeting Minutes AgentRun 中的 JSON insights 仍是原始输入/信号，不直接等同于 MarketInsight。

字段：
- workspace_id
- version
- insight_key
- insight_type（动态文本）
- statement
- assumptions JSONB
- unknowns JSONB
- implications JSONB
- confidence_level：HIGH / MEDIUM / LOW / INSUFFICIENT
- status：DRAFT / NEEDS_REVIEW / ACCEPTED / REJECTED / CONFLICTED / SUPERSEDED
- generated_by_run_id（nullable）
- supersedes_id（nullable）
- insufficient_evidence_reason（nullable；无 Evidence 时必须提供明确缺口说明）
- reviewed_by_user_id / reviewed_at / review_note（审核记录）

约束：
- 每条 Insight 必须能关联一条或多条支持/反驳/上下文 Evidence，或明确 INSUFFICIENT_EVIDENCE。
- CONFLICTED 不得被 UI 显示成确定事实。
- 新版本不能删除旧版本。
- ACCEPTED 表示用户接受为当前工作判断，不代表客观真理。
- AI 输出默认 NEEDS_REVIEW；只有存在至少一条 SUPPORTS 且不存在未解决 CONTRADICTS 时，用户才可采纳。
- 无 Evidence 的判断只能显式标记证据不足，不能被采纳；同一 insight_key 同一 Workspace 最多一个 ACCEPTED 版本。

### 3.8 MarketOpportunity

用途：将有证据的商业机会持久化管理，并连接候选实体及实际差旅行动。

状态机：

~~~text
DISCOVERED → RESEARCHING → EVIDENCE_READY → QUALIFICATION_PENDING
    → QUALIFIED → ACTION_PLANNED → {WON | LOST | DISQUALIFIED | ON_HOLD}
~~~

允许的重新研究/退回路径必须由 Service 显式定义并写入活动事件；禁止客户端任意 PATCH status。

字段：
- workspace_id
- title
- description
- status
- next_action
- qualification_reason
- disqualification_reason
- assessment_version
- assessment_dimensions JSONB
- created_by_run_id
- owner_user_id
- qualified_by_user_id / qualified_at / qualification_reason（人工资格确认审计）
- outcome_recorded_by_user_id / outcome_recorded_at / outcome_note（WON/LOST 结果审计）
- assessment_confidence（HIGH / MEDIUM / LOW / INSUFFICIENT）
- assessment_version（当前评估版本号）

评估历史必须单独追加到 MarketOpportunityAssessment（organization_id、workspace_id、opportunity_id、version、dimensions、summary、confidence_level、assessed_by_run_id / assessed_by_user_id、created_at），不得覆盖旧版评估。

推荐基础评估维度：Strategic Fit、Demand Evidence、Commercial Potential、Accessibility、Competition、Timing、Execution Cost。每个维度结构包含 value（可为 UNKNOWN）、rationale、evidence_refs、unknowns。禁止将缺失值悄悄转为 0 并参与总分。

分数仅用于辅助排序；在获得跨行业校准数据前，不得显示为成交概率。

资格边界：
- DISCOVERED 不代表真实需求。
- EVIDENCE_READY 代表达到研究覆盖门槛，不代表机会成立。
- QUALIFIED 必须由用户确认或明确授权的业务规则确认。
- WON / LOST 必须有实际业务事件或用户说明。
- 会议纪要中的商机信号只能创建候选/建议，不得直接晋级为 QUALIFIED。

---

## 4. Evidence 兼容扩展与版本语义

### 4.1 继续复用 evidence 主表

现有 Evidence 表继续作为唯一证据存储。增量字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| published_at | timestamptz nullable | 来源发布日期；未知必须 NULL |
| canonical_ref | text nullable | 原始规范来源地址/文件引用 |
| content_hash | text nullable | 内容版本 SHA-256 |
| observation | text nullable | 从来源中实际观察到的事实片段/短摘录 |
| verification_status | text nullable | UNVERIFIED / PARTIALLY_VERIFIED / VERIFIED / DISPUTED / REJECTED |
| quality | JSONB nullable | Authority / Freshness / Specificity / Corroboration / Consistency 的维度评估 |

旧 Evidence 行允许这些字段为 NULL；旧调用方无需迁移 payload 即可继续读取。

### 4.2 不破坏旧唯一约束

现有唯一约束 (organization_id, subject_type, subject_id, kind, ref) 和 record_many() 的 ON CONFLICT ON CONSTRAINT 行为必须保留。

市场来源证据采用显式版本化 ref：
- canonical_ref 保存真实来源地址；
- content_hash 保存内容 SHA-256；
- ref 使用稳定版本引用（例如 market-source:v1:<sha256>），不再直接用 canonical URL 作为版本 ref；
- URL 只做保守规范化：限定 HTTP(S)、统一主机大小写/IDNA 与默认端口、去 fragment；不擅自删除 query 参数或改写路径语义；
- 相同 canonical_ref + content_hash 在同一 Workspace 下幂等；
- 同一 canonical_ref 的内容变化产生新 content_hash 和新 Evidence 行；
- 不同来源不得因内容恰好相同而丢失来源差异；版本 identity 至少包含 canonical source identity 与 content hash；
- 不改写、覆盖或删除旧 payload。

现有建议/规划证据继续按旧 ref 语义写入。不得直接删除旧 unique constraint，也不得把旧 record_many 行为改成覆盖更新。

### 4.3 Evidence 关联表

新增两张显式关联表：
- MarketInsightEvidence：organization_id、insight_id、evidence_id、relation_type（SUPPORTS / CONTRADICTS / CONTEXT）、rationale、created_at。
- MarketOpportunityEvidence：organization_id、opportunity_id、evidence_id、relation_type（SUPPORTS / CONTRADICTS / CONTEXT）、rationale、created_at。

每个关联表需：
- 通过复合外键确保 Insight/Opportunity 与 Evidence 的 organization_id 相同；
- 对 (organization_id, target_id, evidence_id, relation_type) 设置唯一约束；
- 不以万能 target_type/target_id 替代明确外键；
- 保留删除保护，删除 Evidence 不得静默让被接受的 Insight 失去依据。

Market Workspace 级来源可通过现有 Evidence subject 多态关联（subject_type = market_workspace, subject_id = workspace_id）保存；Insight/Opportunity 的证据角色则走显式关联表。

---

### 4.4 ResearchPlan 的持久化表达

ResearchPlan 不是独立 ORM 主表。现有用户意图驱动研究以 WorkflowInstance（workflow_type = research_plan）作为单次研究运行记录，研究问题、检索意图和来源范围保留在该 Workflow 的版本化 payload / AgentRun 输出中；外部检索仍使用现有 BackgroundJob，并将初始来源结果写入 Evidence。

MarketWorkspace 通过 MarketWorkspaceWorkflow 关联一个或多个 ResearchPlan Workflow。Workspace 不复制检索查询、Job 或 Evidence 内容；它只负责长期组织研究目标、上下文版本、问题和后续洞察/机会。


### 4.5 Workspace ResearchPlan 的版本化 Evidence 归属

Research Worker 在 Workspace 绑定模式下必须将来源内容版本保存在现有 Evidence 表：
- subject_type = `market_workspace`，subject_id = workspace_id；
- canonical_ref = 保守规范化来源 URL；
- content_hash = 捕获内容（当前检索结果标题 + 摘要）的 SHA-256；
- ref = `market-source:v1:<sha256(canonical_ref + newline + content_hash)>`；
- verification_status 固定为 UNVERIFIED，quality 初始为 NULL；
- 不将搜索摘要描述为全文，不将检索结果提升为 VERIFIED。

新增 `MarketResearchPlanEvidence` 只记录归属关系，不复制 Evidence 内容：organization_id、workspace_id、research_plan_id、evidence_id、search_query、query_sha256、collected_by_run_id、created_at。复合外键分别约束到同 Workspace 的 MarketWorkspaceWorkflow、同组织 Evidence 和同组织 AgentRun；每张组织表在同迁移中 FORCE RLS。

幂等与版本语义：
- 同 Workspace + 同 canonical_ref + 同 content_hash：跨 ResearchPlan 复用同一 Evidence 行；
- 同来源内容变化：插入新 Evidence 版本，旧版本不可覆盖；
- 同一 Evidence 被多个已审阅 query 命中：保存多个 query 关系，Evidence 正文只存一份；
- 同 query / 同 Evidence / 同 ResearchPlan 的关联写入幂等；
- ResearchPlan 详情合并旧 `subject_type = RESEARCH_PLAN` Evidence 与新关联的 Workspace Evidence；
- 没有 Workspace 的独立 ResearchPlan 保持原旧 Evidence 兼容路径。

此关联表不会代替 MarketInsightEvidence / MarketOpportunityEvidence。ResearchPlanEvidence 说明来源被哪次研究/查询发现；Insight/OpportunityEvidence 才说明来源对某条判断或机会起支持/反驳/上下文作用。

## 5. 关系表与既有业务对象连接

首批关系表：

| 表 | 关系 |
|---|---|
| MarketQuestionDependency | question → depends_on_question；同 Workspace；禁止循环 |
| MarketOpportunityEntity | Opportunity ↔ 当前 Workspace 已关联的一个或多个 MarketEntity |
| MarketEntityCustomerLink | MarketEntity ↔ 现有 Customer，记录 MATCH_CANDIDATE / USER_CONFIRMED / REJECTED / CONFLICTED |
| MarketWorkspaceTripLink | Workspace ↔ 现有 Trip，多对多；解除保留关联历史，不删除 Trip |
| MarketWorkspaceWorkflow | Workspace ↔ 现有 WorkflowInstance，多对多；ResearchPlan 是其中一种 workflow_type |
| MarketResearchPlanEvidence | Workspace 级版本化 Evidence ↔ ResearchPlan + 搜索 query + 采集 AgentRun 的来源归属关系 |

关联规则：
- TargetAccount 不新建，使用 MarketEntity + 用户确认的 Customer link。
- Mission 不新建，使用现有 Trip。
- VisitOutcome 不新建，继续使用 VisitRecord / Meeting Minutes / Trip Outcome。
- MarketEntity 与正式 Customer 的链接必须明确记录匹配依据、确认人和确认时间。
- Customer 的现有 A/B/C tier 不等于 Opportunity 阶段或证据等级。
- Trip 与 Workspace 解耦；任何 Workspace 删除/归档都不得级联删除 Trip、Customer、Place、Visit 或既有 Evidence。

---

## 6. 状态变更与审计

新增 append-only MarketActivityEvent（组织级）记录：
- workspace_id；
- actor_type（USER / SYSTEM）；
- actor_user_id（nullable）；
- aggregate_type / aggregate_id；
- event_type；
- payload JSONB；
- created_at。

用途：
- 上下文版本创建/采纳；
- 问题创建、重排、回答、阻塞；
- Entity 发现、验证、合并候选、用户拒绝；
- Insight 接受、驳回、冲突、被替代；
- Opportunity 资格确认与阶段变化；
- Customer / Trip 关联与解除。

事件仅追加，不提供普通 UPDATE/DELETE API。敏感信息不得复制进事件 payload；事件保留最小必要字段与引用。

现有 Outbox 继续负责可靠异步投递；MarketActivityEvent 是领域审计时间线，不替代 Outbox。

---

## 7. API 语义（契约级）

建议资源路径：

- POST /v1/market/workspaces
- GET /v1/market/workspaces
- GET /v1/market/workspaces/{workspace_id}
- PATCH /v1/market/workspaces/{workspace_id}（只允许名称、目标、设置等可编辑字段，不允许任意改状态）
- POST /v1/market/workspaces/{workspace_id}/contexts（创建新快照版本）
- GET /v1/market/workspaces/{workspace_id}/contexts
- POST /v1/market/workspaces/{workspace_id}/contexts/{context_id}:adopt
- GET /v1/market/workspaces/{workspace_id}/questions
- POST /v1/market/workspaces/{workspace_id}/questions
- PATCH /v1/market/questions/{question_id}（只允许 question、rationale、priority、acceptance criteria 等字段）
- GET /v1/market/workspaces/{workspace_id}/entities
- GET /v1/market/workspaces/{workspace_id}/insights
- GET /v1/market/workspaces/{workspace_id}/opportunities
- GET /v1/market/workspaces/{workspace_id}/activity

所有写接口：
- 使用 Idempotency-Key；
- 调用业务 Service/UseCase；
- 检查当前组织 membership 和角色；
- 检查资源 organization_id 与父 Workspace 一致；
- 返回统一错误 envelope；
- 重要状态变更写 MarketActivityEvent；
- 不将模型推断直接保存为用户确认状态。

本地模式未实现市场研究存储时，必须返回明确的本地不支持状态或功能不可用反馈，不允许假装远程写入成功。

---

## 8. H1 实施顺序与冻结范围

### H1.1 — 契约冻结（本文件）

冻结实体、状态机、关系、Evidence 版本策略、租户关系约束和 API 语义。

### H1.2 — 后端核心基础

建议按可独立验收的纵向切片实现：
1. MarketWorkspace + MarketContextSnapshot + MarketResearchQuestion；
2. MarketEntity + Alias + WorkspaceEntity；
3. Evidence 扩展 + MarketInsight + Evidence links；
4. MarketOpportunity + 评估历史/实体关系；
5. Workspace-Trip / Workflow links + MarketActivityEvent。

每个切片均包括 ORM 注册、迁移、RLS、Service、Route、OpenAPI、PostgreSQL 集成测试和跨租户测试。

### H1.3 — iOS 工作空间基础

Workspace 列表/创建/详情、上下文复核、研究问题管理；复用现有 DesignSystem、Endpoint、Repository 与 AppEnvironment 组合根。

### H1.4 — 回归与交付

执行并记录项目后端、API 契约、E2E、iOS build/test 门禁。未执行命令不得标记通过。

---

## 9. H1.1 冻结检查表

- [ ] Workspace 可独立于 Trip 存在。
- [ ] Raw input 不可被 AI 覆盖；抽取每次生成新版本。
- [ ] 用户 override 的字段在后续抽取中优先保留。
- [ ] Industry / entity_kind / customer_segment / business_model 不受固定行业枚举限制。
- [ ] Evidence 现有唯一约束和旧 record_many 调用行为不变。
- [ ] 市场证据通过 content hash 形成不可变版本。
- [ ] Insight 与 Opportunity 都可以链接支持、反驳和上下文 Evidence。
- [ ] MarketEntity 不等于 Customer；Trip 不等于 Workspace。
- [ ] 所有跨表关联均由 organization_id 复合约束或等价数据库保证同租户。
- [ ] 所有新组织级表在同一 migration 中 ENABLE + FORCE RLS。
- [ ] 所有新模型在 models 注册入口登记，Alembic check 能发现漂移。
- [ ] 新 Worker 类型必须同时注册 job type 与 TASK_HANDLERS。
- [ ] 用户可看到来源、证据冲突、未知项和状态变更来源。
- [ ] 现有 Trip 核心闭环不依赖 Market 模块运行。

**冻结状态：** 本文件是 H1.1 契约候选基线。开始 DDL 前应由实现者逐项核对 ORM 关系和 PostgreSQL 复合外键可行性，并在代码评审中记录任何偏离原因。
