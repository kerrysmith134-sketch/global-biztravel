# Sprint H1.0 — 代码与领域模型审计报告

> 审计对象：kerrysmith134-sketch/global-biztravel  
> 审计分支：main  
> 审计基线：b5548425e97100cd9d3e114eb722208aa1d4b1ff（文档索引更新）；业务代码基线包含 c5e98e2b567f54705a8569d0ef7ced138d7f88b2  
> 日期：2026-11-21  
> 审计性质：基于 GitHub 当前文件内容的源码审查；本轮未在本地 checkout 仓库、未连接项目数据库、未执行测试命令。

---

## 1. 执行结论

**结论：可以进入 H1.1 领域契约冻结；不应另建一套 Evidence / Agent Runtime / Customer / Mission 平行业务模型。**

仓库已有相当完整的基础设施：

- Evidence 已是独立持久化表，可承载不同 subject_type 的证据记录；
- WorkflowInstance / AgentRun / BackgroundJob / Outbox 已覆盖工作流持久化、Agent 结果、长任务、重试和恢复；
- LLM Gateway、工具目录、调用审计已经存在；
- Customer、Place、Trip、Visit、VisitRecord、Trip Outcome 和 Memory 已构成真实出差与客户拜访数据链；
- RLS 与组织隔离已经是现有数据表的统一设计要求。

但目前**没有持久化的通用 Market Workspace / Research Question / Market Entity / Market Insight / Market Opportunity 领域模型**。现有 Trip Outcome 只读聚合会议纪要中的商机信号，并把这些信号标记为待确认；它不是机会管理系统。

H1 正确方向：

1. 复用 Evidence，通过向前兼容迁移补足来源版本、验证状态和质量元数据；
2. 新增明确的 Evidence ↔ Insight / Opportunity 关联；
3. 新增 MarketWorkspace、BusinessContextSnapshot、ResearchQuestion、MarketEntity、MarketInsight、MarketOpportunity；
4. 复用 Customer / Place / Trip / Visit，不建立 TargetAccount / Mission / VisitOutcome 平行主对象；
5. 复用 Workflow / AgentRun / BackgroundJob，但显式登记研究任务类型与工具；
6. 不把 TravelerProfile 当成公司业务画像。

---

## 2. 已检查源码范围

### 后端模型与迁移
- services/api/app/models/__init__.py
- services/api/app/models/workflow.py
- services/api/app/models/geo.py
- services/api/app/models/trip.py
- services/api/app/models/records.py
- services/api/app/models/reliability.py
- services/api/app/models/llm.py
- services/api/app/models/proactive.py
- services/api/app/models/memory.py
- services/api/migrations/versions/0016_workflow_evidence_sync.py
- services/api/migrations/versions/0031_visa_catalog_overrides.py
- services/api/migrations/env.py

### 服务、API 与 Agent 基础
- services/api/app/services/evidence_service.py
- services/api/app/services/customer_service.py
- services/api/app/services/trip_outcome_service.py
- services/api/app/services/job_service.py
- services/api/app/llm/gateway.py
- services/api/app/llm/tools.py
- services/api/app/api/schemas.py
- services/api/app/api/routes/customers.py
- services/api/app/api/routes/trips.py
- services/api/app/main.py

### iOS 与项目基线
- apps/ios/GlobalTravelOS/Features/DesignSystem/DesignSystem.swift
- apps/ios/GlobalTravelOS/Core/Networking/APIClient.swift
- README.md
- docs/design/global-business-travel-os-design-v2.0.md
- docs/review/implementation-status.md

> 上述清单不代表已逐文件审查仓库中所有代码。H1.1 实施前仍需审阅迁移 helper、RLS 测试、Worker 装配入口、测试 fixture、iOS API Repository 和导航注册的实际文件。

---

## 3. 字段级复用矩阵

| 领域能力 | 当前真实落点 | 当前字段 / 语义 | H1 决策 |
|---|---|---|---|
| 证据主表 | app.models.workflow.Evidence | organization_id, subject_type, subject_id, kind, ref, source, payload, retrieved_at, created_at | 复用；最小扩展 |
| 证据写入 | app.services.evidence_service.record_many | ref 非空；整条 item 写入 JSONB payload；唯一键冲突时 DO NOTHING | 复用接口语义但需补版本策略 |
| 工作流 | WorkflowInstance | organization_id, trip_id?, workflow_type, status, payload, job_id?, created_by_user_id | 复用；用 Workspace-Workflow 关联表达研究工作流 |
| Agent 执行 | AgentRun | organization_id, workflow_id?, agent_type, status, input, output, error, started_at, finished_at | 复用；不把它误认为完整 Supervisor |
| 长任务 | BackgroundJob | job type、payload、result、attempts、lease、heartbeat、dedupe key、错误与终态 | 复用 |
| 模型调用审计 | LLMInvocation | task、model、prompt id/version、input hash、output、token、latency、cost、degraded、cached、error、trace id | 复用 |
| Agent 工具 | app.llm.tools.TOOL_CATALOG | 名称、输入 schema、scope、审批要求、幂等、use case handler | 扩展登记，不旁路 |
| 正式客户 | Customer | 组织级；name, external_ref, tier, country_code, industry, encrypted contacts, status, notes | 继续作为正式客户真相源 |
| 客户地点 | CustomerPlace / Place | 一个 Customer 可关联多个 Place；Place 有国家、城市、坐标和地理编码来源 | 复用 |
| 出差任务 | Trip | 组织级；目的地国家、时间窗、目的、状态、预算、版本、active plan | 继续作为 Mission 唯一主对象 |
| 拜访 | Visit | Trip、Customer、Place、目标、优先级、商业价值、时间窗、状态 | 复用 |
| 拜访反馈 | VisitRecord | outcome、加密 notes、photos、follow_up_tasks、离线版本和冲突副本 | 复用 |
| 会议纪要 AI | AgentRun 输出 | MEETING_MINUTES_DRAFT 的结构化 insights | 作为机会信号来源，不等同于机会记录 |
| 出差复盘 | trip_outcome_service.build_outcome | 只读汇总 Visit、费用、纪要 insight、待确认机会信号及记忆 | 复用为反馈入口；增加持久化 MarketOpportunity |
| 记忆 | TravelerProfile / PlanAttemptLog / TripMemorySummary | 出行偏好、规划过程、Trip 摘要 | 复用；不能替代 Business Context |

---

## 4. 关键发现与问题

### H1-F01 — Evidence 可复用，但当前不够支撑完整市场证据治理
**严重度：P1（H1 核心）**

实际模型位于 services/api/app/models/workflow.py。当前有主体、多态主体 ID、kind、ref、source、JSONB payload 和 retrieved_at。subject_type / subject_id 没有指向具体主体表的外键，因此这是一个 polymorphic evidence store，而不是带强关系约束的证据图。

当前缺少一等可查询字段：
- 来源发布日期 published_at；
- 内容版本指纹 content_hash；
- 明确的验证状态；
- 维度化质量评估；
- 结构化观察内容 / canonical source metadata。

当前 evidence_service.record_many() 对相同 organization / subject / kind / ref 使用 ON CONFLICT DO NOTHING。这适合当前建议/计划引用的幂等写入，但会带来市场研究的版本问题：来源 URL 内容更新后，同一 ref 不会自动形成新证据版本，旧 payload 也不会刷新。

**建议：**
1. 保留现有 Evidence 主表，不再新建 MarketEvidence / ResearchEvidence 第二套主表。
2. 在 Evidence 上做兼容扩展：published_at、content_hash、verification_status、quality 等字段；原有记录允许为空。
3. 定义版本语义：相同来源、相同内容 hash 应幂等；相同 canonical ref 但内容 hash 不同，应保存新版本而不是静默覆盖或丢弃。
4. 新增 MarketInsightEvidence 与 MarketOpportunityEvidence 关联表，外键指向 Evidence，并通过 relation_type = SUPPORTS / CONTRADICTS / CONTEXT 表达证据角色。
5. Market Workspace 的 Evidence 仍复用 Evidence 表，但 subject_type 使用稳定的 market workspace 类型值，subject_id 指向 Workspace；关联表再把 Evidence 连到具体 Insight / Opportunity。
6. 既有 record_many 行为不得被静默改成覆盖更新；为市场证据实现明确的版本化写入策略和测试。

### H1-F02 — Workflow / AgentRun 是持久化基座，不是现成的市场研究 Supervisor
**严重度：P1（H2 前置）**

WorkflowInstance.trip_id 是 nullable，当前 workflow 有 workflow_type / status / payload / job_id；AgentRun 通过 workflow_id 关联工作流，输入输出以 JSONB 持久化。

BackgroundJob 已有在途 dedupe、租约、心跳、指数退避和 DEAD 终态。这足以承载长时间研究的持久化执行基础。

job_service.KNOWN_JOB_TYPES 目前登记：
- plan_trip
- draft_minutes
- crm_sync
- fx_rate_sync

app.llm.tools.TOOL_CATALOG 当前登记 5 个既有工具，主要是读取行程计划、准备签证清单、准备地点地理编码、准备计划调整、起草会议纪要。

因此：
- 当前基础设施可以复用；
- 研究 job handler、Research Tool、Research Planner、Evidence Validator、Entity Resolution、Opportunity Qualification 尚需实现和登记；
- 不能仅因为已有 AgentRun 表就宣称已具备多 Agent 研究运行时。

**建议：**
- WorkflowInstance 继续作为研究工作流根；
- 用 MarketWorkspaceWorkflow 关联表把 Workspace 与一个或多个 Workflow 关联，不强迫研究工作流依赖 Trip；
- 研究阶段由显式 ResearchQuestion 和任务依赖描述；
- Job / AgentRun 都记录 workspace_id、research_question_id 或通过强关系关联，避免只靠 payload 查询；
- 每个新 Tool 均声明 scope、审批要求、幂等性和业务 use case handler。

### H1-F03 — Customer 已经是稳定主数据；候选实体必须与正式客户分离
**严重度：P1（数据正确性）**

Customer 位于 app.models.geo，而不是独立 customer.py。它是组织级资源，状态为 ACTIVE / INACTIVE / PROSPECT，客户层级 A / B / C，industry 是 nullable Text，不是硬编码行业枚举。联系人姓名、邮箱、电话使用 EncryptedText。

customer_service 的自动复用规则：
1. 优先 external_ref；
2. 再按规范化名称精确匹配，必要时结合国家；
3. 不按名称模糊相似度自动合并。

这条规则是正确的保守策略，应保持。

**建议：**
- 新增 MarketEntity 作为市场研究的候选企业/机构/人物/产品/项目实体；
- Industry、entity kind、customer segment、business model 均使用动态分类，不建立硬编码行业枚举；
- MarketEntity 与 Customer 通过显式 link / match 状态关联；
- 只有用户确认后才创建或关联正式 Customer；
- 匹配建议可使用域名、注册标识、国家/地区、地址、名称等多信号，但低置信度只产生候选，不自动合并；
- Customer 的 A/B/C 是现有业务分级，不应被挪作行业分类或机会阶段。

### H1-F04 — Trip / Visit / VisitRecord / Trip Outcome 已足够作为出差执行闭环
**严重度：P2（复用建议）**

Trip 已承担出差任务实体；Visit 绑定 Trip、Customer 和 Place；VisitRecord 支持离线记录、幂等和冲突副本。

trip_outcome_service 从已完成的会议纪要 AgentRun 提取分类 insight，包括 CUSTOMER_NEED、OPPORTUNITY_SIGNAL、DEAL_TERM、COMMITMENT 和 STAGE_CHANGE，并将机会信号标记为 requires_confirmation = true。

**缺口：**当前 outcome 是只读聚合，Opportunity 信号没有升级成可持久化、可追踪生命周期的商业机会对象。

**建议：**
- 保持现有 VisitRecord / Meeting Minutes / Trip Outcome；
- 新增 MarketOpportunity，与 MarketEntity、Evidence、Workspace 和现有 Customer / Trip / Visit 建立关系；
- 拜访后的 insight 只能成为 Opportunity 候选或更新建议；必须经确认/业务规则后改变机会阶段；
- 不新建 Mission 或 VisitOutcome 平行对象。

### H1-F05 — TravelerProfile 不是 BusinessProfile
**严重度：P2（模型边界）**

现有 TravelerProfile 存储出差人偏好、国籍、notes、confidence 和有效期；它是 traveler semantic memory，不是企业业务资料。

**建议：**
- H1 不把公司业务描述、产品、服务、价值主张塞进 TravelerProfile；
- 先以 MarketWorkspace 关联的 BusinessContextSnapshot 保存每次研究的原始输入、结构化抽取、unknowns、provenance 和 prompt/schema 版本；
- 是否增加跨 Workspace 的长期 Business Profile，留到有真实复用需求时再设计，且必须由用户控制。

### H1-F06 — 现有 Evidence 与 Agent 输出缺少完整的证据图关联
**严重度：P1（H1 核心）**

Evidence 以一个 subject 为归属；Insight 当前主要作为 Meeting Minutes AgentRun JSON 输出或 Trip Memory 内容，并没有独立的 MarketInsight 领域对象。LLMInvocation 有 trace_id，但 AgentRun 当前没有显式 llm_invocation_id 关系字段。

**建议：**
- 新增 MarketInsight，记录 statement、assumptions、unknowns、status、version；
- 通过独立关联表连接 supporting / contradicting Evidence；
- 必要时增加 AgentRunInvocation 关联表，而不是在一个 AgentRun 上加单个 invocation_id（一个运行可能发起多次模型调用）；
- Insight 的最终内容必须保留 evidence refs 和生成 run 的来源。

### H1-F07 — Worker 与工具目录当前是显式白名单，需要有界扩展
**严重度：P1（H2 前置）**

job_service 的任务类型集中登记，Tool Catalog 通过显式白名单控制 Agent 可调用能力。

**建议：**
- 新增 market_research、market_context_extract 等 job type 前，先审阅 Worker 实际 handler 注册表，避免只加枚举却没有消费者；
- Research Tool 只读优先；任何正式 Customer / Trip 写操作必须复用已有业务 UseCase、人审、权限和幂等机制；
- Research Tool 需增加访问域名/网络边界、超时、重定向、文件大小与内容类型限制，防 SSRF 和恶意内容；
- 外部页面与上传文件均为不可信数据，不能覆盖 System Prompt 或工具权限。

### H1-F08 — 生产客户端默认配置存在发布安全风险
**严重度：P0（生产发布前整改）**

apps/ios/GlobalTravelOS/Core/Networking/APIClient.swift 中可见：
- 内置默认 API-Key；
- 默认组织 ID 与用户 ID；
- 默认 trycloudflare.com 临时隧道地址。

服务端 main.py 将 GBT_API_TOKEN 作为部署级门禁。把同一个部署 token 编译进 iOS 应用，不能作为生产秘密，因为客户端包可被提取；固定组织/用户 ID 也不能代替真实用户身份验证。临时隧道 URL 适合开发或 TestFlight 联调，不应作为生产默认值。

**建议：**
- 生产环境改用真正的用户认证与短期凭证；部署级 token 不得作为客户端内置秘密；
- 默认组织/用户 ID 仅允许在明确的 Debug / Demo 配置下使用；
- 生产 build 不得包含 trycloudflare 临时域名；
- 增加 CI 检查，防止生产配置带入 demo token、固定租户 ID 或开发隧道；
- 该项独立于 H1，但必须进入生产发布安全清单。

### H1-F09 — Alembic 文档漂移已定位
**严重度：P2（迁移治理）**

源码确认 services/api/migrations/versions/0031_visa_catalog_overrides.py 存在，revision 为 0031_visa_catalog_overrides，其 down_revision 是 0030_traveler_nationality。模型注册入口也导入 VisaCatalogOverride。

README 当前目录说明仍停留在 0030_traveler_nationality，而 docs/review/implementation-status.md 已列出 0031_visa_catalog_overrides。

因此不是迁移文件缺失，而是 README 落后于实际源码。H1.1 不能再把“0030/0031 冲突”当作未解决问题。

**建议：**
- 更新 README 迁移说明为 0031_visa_catalog_overrides；
- H1.1 开始实施时仍需在实际数据库环境运行 alembic heads / alembic current，因为源码 revision 图不能证明某个部署数据库已经升级到 head；
- 新迁移从实际 0031 revision 往后接，不得重写历史迁移。

---

## 5. H1.1 建议逻辑数据模型（根据源码审计修订）

以下是下一阶段要冻结的候选结构，不是已执行的数据库迁移。

### 5.1 新增核心表

| 表 | 目的 | 关键关系 |
|---|---|---|
| market_workspaces | 一次独立的市场探索 | 组织、创建人、状态、当前 context version |
| market_context_snapshots | 原始输入 + 抽取版本 + 用户修订 + provenance | FK → workspace |
| market_research_questions | 用户问题与 Planner 拆解任务 | FK → workspace；可有父问题/依赖 |
| market_entities | 公司、人物、产品、机构、地点、项目候选 | 组织级；动态 entity_kind |
| market_entity_aliases | 名称、域名、外部 ID、语言别名 | FK → entity |
| market_workspace_entities | 记录实体进入 Workspace 的发现来源与研究状态 | Workspace ↔ Entity |
| market_insights | 有版本的研究判断、假设、未知与冲突状态 | FK → workspace |
| market_opportunities | 持久化商业机会与分维度评估 | FK → workspace；可关联一个或多个 entity |
| market_insight_evidence | Insight ↔ Evidence 关联及支持/反驳类型 | FK → insight / evidence |
| market_opportunity_evidence | Opportunity ↔ Evidence 关联及支持/反驳类型 | FK → opportunity / evidence |
| market_entity_customer_links | 候选实体与正式 Customer 的匹配/确认记录 | FK → entity / customer |
| market_workspace_trip_links | Workspace 与既有 Trip 关联 | FK → workspace / trip |
| market_workspace_workflows | Workspace 与既有 WorkflowInstance 关联 | FK → workspace / workflow |

### 5.2 Evidence 扩展建议

在现有 evidence 表向前兼容地增加：
- published_at timestamptz NULL；
- content_hash text NULL；
- verification_status text NULL（服务端校验，避免把领域状态写成固定 DB enum）；
- quality jsonb NULL；
- 必要时 canonical_ref text NULL，保留 ref 原有语义，不改变既有调用方。

版本唯一性需要单独设计：现有唯一约束包含 organization_id / subject_type / subject_id / kind / ref，不能原样保留又期待同一 ref 多版本。建议新旧数据兼容策略为：旧记录继续遵守 legacy uniqueness；新市场证据按内容 hash 形成版本；同一来源/同一 hash 幂等，不同 hash 留存新版本。H1.1 必须先为 PostgreSQL partial/expression unique index 写迁移与并发测试，不能先改服务后补约束。

Evidence 的原始 payload 必须保留，版本化不能覆盖历史来源内容。published_at 未知时保持 NULL，不能由 AI 猜测。

### 5.3 关系和约束

- 每张组织级表都必须 organization_id、RLS ENABLE + FORCE、应用角色权限及跨租户测试。
- Workspace → ContextSnapshot 使用版本号唯一约束；用户采纳版本通过显式字段/事件表达。
- ResearchQuestion 依赖图必须防循环。
- MarketEntity 的规范化不能破坏原始名称、域名和来源。
- MarketOpportunity 每次评估保存规则版本、维度依据和 Evidence refs。
- 所有多对多关系必须有唯一约束；所有有业务意义的写入必须幂等。
- 跨组织关联 Customer / Trip / Evidence 时，除应用层检查外，必须确保不能建立跨租户外键关系；优先使用包含 organization_id 的复合约束或等价安全方式。
- 删除 Workspace 时不能意外删除正式 Customer、Trip 或现有 Evidence；先定义 archive / retention / cascade 规则。

---

## 6. H1.1 开始前需要冻结的 5 个决策

1. **Evidence 版本唯一性**：旧记录按原 unique key 保持；新市场证据采用内容 hash 版本化，旧调用方行为不变。
2. **证据关联关系**：Insight 与 Opportunity 各自使用显式关联表，保留 SUPPORTS / CONTRADICTS / CONTEXT，不使用无外键的万能 target_type / target_id 作为唯一关系。
3. **候选实体与 Customer**：候选公司/机构首先是 MarketEntity；用户确认后才关联/创建正式 Customer。
4. **研究工作流关系**：复用 WorkflowInstance + AgentRun + BackgroundJob，通过 Workspace-Workflow 关联表表达工作空间上下文；Trip 不必存在。
5. **业务上下文画像**：H1 暂不加全局 BusinessProfile，先采用 Workspace 版本快照，避免把不同产品线/不同市场的上下文错误合并成账户级永久画像。

---

## 7. 尚未验证与禁止推断

本轮未连接真实数据库，因此以下状态不能据源码推断：
- 各部署数据库当前 alembic current；
- 生产环境实际启用的 RLS policy 与运行角色权限；
- Worker 生产进程是否已注册所有 KNOWN_JOB_TYPES；
- iOS 远程 Repository、导航入口和离线缓存的全量实现细节；
- 所有自动化测试在当前最新 main 是否通过；
- 新增 Market 模型的实际性能与索引成本。

开始 H1.1 前应先完成 Worker handler 装配、RLS 测试 helper、iOS API Repository 和 navigation registration 的文件级审查。

---

## 8. 进入 H1.1 的验收门槛

- [ ] Evidence 扩展方案兼容旧 record_many() 调用和既有建议/计划证据。
- [ ] MarketInsight / MarketOpportunity 的所有重要结论可关联 Evidence 与反证。
- [ ] MarketEntity 不自动写入 Customer 主数据。
- [ ] Trip 不要求绑定 MarketWorkspace。
- [ ] Workspace 与 Workflow 可以没有 Trip 而独立运行。
- [ ] 新行业/实体类型不受代码枚举限制。
- [ ] 新表均纳入模型注册入口、Alembic、RLS、API 契约和测试。
- [ ] 迁移从 0031_visa_catalog_overrides 后向前新增，不改历史迁移。
- [ ] 现有 make test / typecheck / check / contract-check / e2e / ios-build / ios-test 回归结果有真实执行记录。
- [ ] 生产客户端内置部署 token / 固定身份 / 临时隧道列入独立安全整改。

**H1.0 结论：可进入 H1.1；先冻结 Evidence 版本与关联设计，再写 DDL。**


---

## 9. H1.0 集成接线审查（补充）

本节补充第二轮对 Worker、RLS helper、API 装配、iOS Repository 组合根和导航的源码核对。

### 9.1 模型注册与 Alembic 漂移门禁

- `services/api/app/models/__init__.py` 是模型注册入口；新增 ORM 模型必须导入并加入导出列表，否则 `migrations/env.py` 的 Base.metadata 不会包含它，Alembic drift check 可能漏报。
- `services/api/migrations/env.py` 将 `Base.metadata` 设为 target_metadata，且 migrations 以显式 DDL 为准；`make check` 用于核对模型/数据库漂移。
- 新迁移必须沿用 `services/api/migrations/helpers.py` 的 `enable_org_rls(table, force=True)`，该 helper 会创建 `tenant_isolation` policy，并在角色存在时授予 `gbt_app` DML 权限。
- 应用会话通过 `app.db.bind_org()` 和事务级 GUC 设置 `app.current_org`；RLS 与应用层组织过滤应同时保留。

### 9.2 API 与权限装配

- `services/api/app/api/routes/__init__.py` 明确汇总所有资源 router；新增 Market router 必须在此 import 并 include。
- `services/api/app/main.py` 只装配 `api_router`，不应直接在 main.py 编写市场业务逻辑。
- `services/api/app/api/deps.py` 每个请求从 X-Org-Id / X-User-Id 解析身份，并验证 membership；关闭开发身份模式后，身份头缺失会 401。所有新 Market 路由必须调用现有 `require_role`。
- 写接口应沿用 `IdempotencyKeyDep` + `IdempotentWrite`；列表接口沿用统一 Page/cursor 语义。
- `contracts/openapi.yaml` 是服务端契约文件；实现后必须通过项目既有 OpenAPI 导出及 contract-check 流程同步，而不是手工维护另一套契约。

### 9.3 Worker 注册是双重白名单

实际检查 `services/api/app/workers/main.py`、`services/api/app/workers/tasks.py` 和 `services/api/app/services/job_service.py`：

- Worker 通过 `TASK_HANDLERS` 查找实际 handler。
- 找不到 handler 会把 job 终态标记失败，不会静默等待。
- 任务类型还由 `job_service.KNOWN_JOB_TYPES` 集中登记。

因此每种新增异步研究任务必须同步更新任务类型常量、入队逻辑、TASK_HANDLERS 和对应 handler 测试。不能只创建 BackgroundJob 就宣称任务可以被 Worker 执行。

### 9.4 iOS 数据接线与信息架构

实际检查：
- `apps/ios/GlobalTravelOS/Core/Networking/APIClient.swift`
- `apps/ios/GlobalTravelOS/Core/Networking/Endpoints.swift`
- `apps/ios/GlobalTravelOS/Data/Repositories/CustomerRepository.swift`
- `apps/ios/GlobalTravelOS/App/AppEnvironment.swift`
- `apps/ios/GlobalTravelOS/App/RootView.swift`
- `apps/ios/GlobalTravelOS/Features/Customers/CustomersView.swift`
- `apps/ios/GlobalTravelOS/Features/DesignSystem/DesignSystem.swift`

确认的模式：
- Endpoint 定义集中在 Endpoints.swift；每个写请求应生成幂等键。
- Repository 有 Remote 与 Local 实现；不能只新增远端实现而忘记本地模式的行为边界。
- AppEnvironment 的 RepositoryBundle / makeBundle / apply 是组合根；新 Repository 必须在 cloud/local 两种模式中有明确实现或明确的 pending error 行为。
- RootView 当前 Tab 为 Today、Trips、Customers、Action Center、Settings；尚无 Market workspace 根入口。
- 新功能必须复用现有 DesignSystem。若 H1 本地模式尚不支持 Market Workspace，必须明确显示不可用/需联网，不能静默调用云端或伪造本地成功。

### 9.5 测试与验证边界

- 已读取 `services/api/tests/test_trip_outcome.py`，确认集成验收采用 TestClient + 真实组织身份头 + org_session，测试中覆盖跨租户 404。
- 已读取 Makefile，项目门禁包含 `make test`、`make typecheck`、`make lint`、`make check`、`make contract-check`、`make e2e`、`make ios-build`、`make ios-test` 和 `make ci`。
- 本次 GitHub 文件审查没有在仓库工作区运行上述命令，也没有连接部署数据库；不能声称测试通过或 alembic current 已确认。

### 9.6 H1.1 实施依赖顺序

1. 先冻结 MarketWorkspace / ContextSnapshot / ResearchQuestion 的 schema、状态机、版本和唯一约束。
2. 新模型文件加入 models 注册入口。
3. 编写从 `0031_visa_catalog_overrides` 向前的显式 Alembic migration，创建组织级表并立即启用 RLS。
4. 实现 Service / UseCase / Router，Router 仅做 HTTP 语义与权限门禁。
5. 注册 Market router，导出 OpenAPI，执行 contract-check。
6. 补真实 PostgreSQL 集成测试、RLS 隔离、幂等及状态迁移测试。
7. 补 Swift models、Endpoints、Repository、AppEnvironment 组合根和 UI。
8. 执行项目门禁并将未执行项明确列出。

**补充结论：**H1.0 的源码审查范围已覆盖后端核心模型、迁移 helper、API 权限与路由装配、Worker 双重注册、iOS Repository 组合根和导航接线。仍未完成的是本地/部署数据库状态检查以及实际测试运行；这些是工程验证项，不应以 GitHub 文件读取代替。


---

## 10. 与现有用户意图 ResearchPlan 的整合审查

在审计后进一步检查尚未合并的 ui-foundation-2026-11 分支与 Draft PR #1。该分支已实现通用 ResearchPlan：
- 用户自然语言请求驱动研究计划生成；
- 内部/外部资料范围明确选择；
- 规划与外部检索分离；
- 搜索查询由用户审阅并确认后才执行；
- SearXNG Provider 默认不可用，未配置时明确 503；
- Worker 只执行确认过的查询，并把搜索结果以未核验状态写入现有 Evidence；
- 复用 WorkflowInstance / AgentRun / BackgroundJob / Evidence，不另造研究执行表。

关联决策见该分支 ADR-0035（研究由用户意图驱动）与 ADR-0037（只执行用户确认查询）。

**整合结论：**ResearchPlan 与 MarketWorkspace 是不同层次，不是重复产品：
- ResearchPlan = 一次研究意图、查询审阅与检索执行的 workflow；
- MarketWorkspace = 持续的市场探索空间，保存上下文版本、研究问题和未来的市场实体/洞察/商机；
- 一个 Workspace 可以包含多个 ResearchPlan Workflow；
- ResearchPlan 允许不关联 Workspace，保留单次研究入口；
- Workspace 不得创建第二套检索 Job、搜索 Provider 或 ResearchPlan 主表。

为此 H1.2 采用 MarketWorkspaceWorkflow 作为显式关联表。它与现有 WorkflowInstance 建立 (organization_id, id) 复合唯一键及复合外键，数据库层保证跨租户关联不成立。Research API 增加可选 workspace_id，并在关联 Workspace 的事务中记录 RESEARCH_PLAN_LINKED 活动事件。新增 Workspace API 可读取其关联的 ResearchPlan 列表。

iOS ResearchPlan 请求 DTO 与 ResearchWorkspaceView 增加可选 workspaceId，默认 nil，以维持原有调用兼容。完整 Workspace 原生列表/详情/Context Review UI 仍属于 H1.3，不能把当前单次 ResearchWorkspaceView 描述成已完成的长期工作空间 UI。

### 10.1 本轮整合的代码文件

- services/api/app/models/workflow.py：增加 (organization_id, id) 复合唯一约束，与迁移保持一致。
- services/api/app/models/market.py：增加 MarketWorkspaceWorkflow。
- services/api/migrations/versions/0032_market_workspace_foundation.py：增加 tenant-safe workflow link 表与约束。
- services/api/app/services/research_service.py：校验 workspace 所属组织、关联 ResearchPlan Workflow、写活动事件、按 Workspace 列出研究计划。
- services/api/app/api/schemas.py / services/api/app/api/routes/market.py：ResearchPlan Create/Read 支持 workspace_id；增加 Workspace ResearchPlan 列表 API。
- apps/ios/GlobalTravelOS/Domain/Models/Research.swift：请求与响应增加可选 workspaceId。
- apps/ios/GlobalTravelOS/Features/Assistant/ResearchWorkspaceView.swift：支持可选传入 workspaceId。
- services/api/tests/test_market_workspace.py：覆盖 Workspace 包含既有 ResearchPlan、external-only 边界、活动事件与直接 RLS 隔离。

### 10.2 仍未验证

本轮仍未在本地 checkout、真实 PostgreSQL 或 Xcode 环境执行命令。未运行迁移、pytest、MyPy、Ruff、OpenAPI contract-check、E2E 或 iOS build/test。文件审查不能替代运行时验证。Draft PR 需以仓库 CI 与真实 PostgreSQL 门禁为准。
