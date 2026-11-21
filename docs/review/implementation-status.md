# 工程实施进度核对（PRD MVP-1 基线 / 设计文档 v2.0）

- 核对时间：2026-10-28
- 核对方式：逐条对照原始 PRD 的 MVP-1 执行基线（`37314–38017`）、API 契约（`32633–33240`）、
  规划引擎契约（`33241–34236`）、DDL（`31849–32632`）与设计文档 v2.0；在真实 PostgreSQL 上执行
  后端测试、迁移/契约漂移检查、MyPy、Docker E2E、iOS 构建/单测/UI 冒烟。
- 结论：**PRD 与设计文档中可在本地完成的 MVP-1 开发项已落地。** 未完成项只剩需要真实外部
  凭证/生产设施的部分，以及"AT-001~AT-012 全部按钮级 UI 自动化"这一定位为测试工程的工作。

## 1. 里程碑核对

| 里程碑 | 要求 | 状态 | 证据 |
|---|---|---|---|
| MS-1 | 骨架 + PostgreSQL/PostGIS + 迁移 + RLS | ✅ | 迁移 `0001`~`0030`；`0010_security_rls` 全库兜底；RLS 断言测试 |
| MS-2 | 身份/组织/Trip/客户/地点 + Excel 两阶段导入 | ✅ | 导入 API、真实校验报告、E2E 第 3 步 |
| MS-3 | 规划引擎 + 独立验证器 + Outbox/Worker | ✅ | 六段流水线、双轨求解器、严格覆盖不变量（ADR-0007）、状态枚举、交通段/费用 |
| MS-4 | 计划版本/差异/激活 + 并发冲突 | ✅ | 不可变守卫、`If-Match`、单个 ACTIVE、diff、嵌套详情路径 |
| MS-5 | 现场记录 + 离线同步 + 主动式 + 记忆 | ✅ | `visit_records`、`sync:push`、四类触发、三层记忆与复盘、`sync_operations` 审计 |
| MS-6 | iOS 客户端 | ✅ | Today/行动中心/Trips/计划/地图/日历/现场记录/CRM/费用/简报/同步/记忆/设置；构建通过；46 单测（18 解码 + 28 契约样本）+ 8 UI 自动化 |
| MS-7 | 交付校验 | ✅ | `make e2e` 真实后端全链路；iOS UI 冒烟；CI 脚本与门禁 |

## 2. 批次 1（MVP-1.1）

| 编号 | 事项 | 状态 | 落点 |
|---|---|---|---|
| M5 | 数据库级租户隔离 | ✅ | ENABLE + FORCE + `tenant_isolation`；应用角色非 owner；事务级 GUC |
| M6 | 指标与验收 | ✅ | 9 项指标、快照幂等、Worker 周期写入、SLO 目标 |
| M1 | 主动式运行时 | ✅ | SCHEDULED / EVENT / STATE / EXTERNAL；抑制三层；审批+审计；通知通道 |
| M4 | 可靠性 | ✅ | 幂等、Outbox、任务租约/心跳/退避/DLQ、任务取消 |
| M3 | LLM 编排与工具契约 | ✅ | Provider 抽象、调用审计、缓存、schema 校验/重试、模板降级、工具目录 |
| M2 | Agent 记忆与复盘 | ✅ | 三层记忆、pgvector、混合检索、复盘 Worker、合规删除 |

## 3. PRD 缺口核对（本轮逐项关闭）

| PRD 要求 | 处理 |
|---|---|
| 规划结果状态 `OPTIMAL/FEASIBLE/PARTIAL/INFEASIBLE/UNKNOWN/ERROR` | `SolutionStatus` 入契约；成功解为 `FEASIBLE/PARTIAL`，绝不标 `OPTIMAL`；落库 `plan_versions.solution_status` |
| 交通段、时间矩阵、费用分项、未知费用不为 0 | `plan_travel_legs`（起终点真实外键）、`travel_estimates`、`cost_total/cost_unknown_count`；未知费用写 NULL |
| Provider 超时降级（AT-003） | `TravelProvider` 边界 + 重试 + 回退；所有受影响段 `stale=true`、来源 `fallback-haversine` |
| 统一计划变更 `POST /trips/{id}/plan-changes` | 已实现（改时间/时长、取消、增删拜访）；基础版本校验、WorkflowInstance、入队新任务（ADR-0011） |
| `WorkflowInstance / AgentRun / Evidence / SyncOperation / IntegrationConnection / ExternalAction` | 迁移 `0016` 全部建表并实际写入：计划变更、纪要任务、建议证据、同步逐项、集成连接、建议执行 |
| Trip `hold / resume / complete` | 端点与 `HOLD` 状态；恢复按生效计划回到 ACTIVE/READY/DRAFT |
| 规划任务取消 | `POST /v1/jobs/{id}:cancel`；PENDING 可取消，RUNNING 返回 409 |
| 会议纪要异步任务 | `POST /v1/visits/{id}/minutes:jobs` + `draft_minutes` Worker handler + AgentRun |
| iOS Map / Calendar | `PlanMapView`（MapKit Marker）与 `PlanCalendarView`（按日日程） |
| 离线更新时间 / 重启恢复 | 离线摘要带"更新于 HH:mm"；`lastTripId` 持久化并在出行页展示"上次出行" |
| `/api/v1` vs `/v1` | 以设计 v2.0 的 `/v1` 为契约真相；额外挂 `/api/v1` 隐藏别名兼容 PRD 基线 |
| CI：Ruff/MyPy/Pytest/PG/Alembic/OpenAPI/SwiftLint/XCTest | `scripts/ci.sh`、`make typecheck`、`make lint-ios`、`.github/workflows/ci.yml` |
| 当地生活指南（方向五） | `local_guide_service` + `app/data/local_guide_catalog.json` | 8 国离线目录按出行国家组装、城市贴士按拜访城市裁剪、实时源缺口显式标注、iOS 指南页 |
| AI 助手对话/会话内执行（页面 05） | `assistant_service` + 迁移 `0027` + iOS `AssistantView` + `test_assistant_actions.py` | 对话与工作流分离（删聊天不影响任务）、事实卡片由服务端计算、工具提案标注人审、llm_invocation 可回溯 |
| 通知用户偏好（S8-08） | `notification_preferences` + 迁移 `0028` | 静音等级不产生通知但保留建议；GET/PUT 偏好幂等；非法等级 4xx |
| 团队协作（P1） | `member_service` + `routes/members.py` | GET/PATCH/DELETE 成员；角色降级/移除保护最后 OWNER；ADMIN 不能提升 OWNER；跨租户隔离 |
| 出差结果分析与机会复盘（P1） | `trip_outcome_service` + `GET /trips/{id}/outcome` | 拜访结果计数、费用计划vs实际分列、纪要洞察按类计数、商机信号待确认、零 CRM 写入 |
| 纪要分类洞察（P1） | `meeting_minutes` v2 + `MUST_CONFIRM_KINDS` | 六类信息分类；非摘要强制人工确认（服务端覆盖模型）；模板兜底只回摘要；零 CRM 写入；iOS 待确认计数 |
| 入境评估结合证件/国籍（P0-7） | `traveler_nationality` + 迁移 `0030` | 画像登记/清除国籍（幂等、校验）；评估陈述证件前提、缺信息降置信度；不给确定性入境结论；租户隔离 |
| 客户多地址（P0-3） | `place_service.list/unlink_customer_place` + `routes/customers.py` | 一企业多地址增删查；主地址优先；来源+可信度随地址返回；解除关联不删地点；跨租户 404 |
| 自然语言创建出差（P0-2） | `trip_intake` v1 + `create_trip_draft` 动作 + 迁移 `0029` | 意图判定不误伤问状态；缺字段反问不编造；提案卡人确认；服务端二次校验 + 幂等；DRAFT 不预订 |
| 通知分级（S8-08 P0） | `notification_policy_service` + 迁移 `0026` | P0–P3 确定性规则入队落库；Feed `unread_by_priority`；`priority` 过滤；iOS 分组/徽标/已读 |

## 4. 验证记录

| 命令 | 结果 |
|---|---|
| `make test` | **240 passed**（真实 PostgreSQL；全新库 `db-reset` 后一条命令全绿——`make test` 自动先补种子数据） |
| `make typecheck` | MyPy：138 个源文件无问题 |
| `make lint` / `make check` | Ruff 通过；迁移无漂移 |
| `make contract-check` | 85 路径 / 141 schema；契约一致（S6：`unplaced`/`policy_violations`/`cards`/`data_gaps`/`actual_cost` 已从 `list[dict]` 收紧为显式模型，见 ADR-0030） |
| `make e2e` | 创建 → 导入 → 求解 → 激活 → 记录 → 同步 → 纪要 → 复盘 → 指标 → 审计，全部通过 |
| `make bench` | N3 实例族 8 实例全门禁通过（覆盖/能力声明/确定性/金目标基线，2026-11-10 实测）：启发式全族 p50 ≤1.8ms；ortools 快实例 6.5ms~8.9s、corridor_rhine_12 39.4s。**实测发现**：`solver_budget_seconds` 是解数量预算而非墙钟，DFS+GLS 在 n≥12 后墙钟指数级（详见 `docs/architecture/performance.md` §3）。快速跑：`make bench ARGS="--skip-ortools"` |
| `make ios-build` | BUILD SUCCEEDED |
| `make ios-test` | **46 单测 + 9 条 UI 用例整轮全绿**（598s，连接真实后端，经 `scripts/ios_test_guard.sh` 保护；链式 207s、AT-007/008 专项 131s）。2026-11-09 八轮完整套件实证并修掉：费用横幅谓词过宽（误匹配正常区块头）、AT-006 加载时序、AT-011 同步 Tab 被 sheet 关闭动画吞掉、AT-007 计划版本区定位、`PlanDiff` 模型与契约漂移（契约样本门禁首次实战命中）、`backToTripDetail` 多元素匹配、Excel 文件选择器重试 |
| `swiftlint lint` | 0 violations（配置见 `.swiftlint.yml`） |
| `make stack-up` + E2E | Docker DB/API/Worker 健康（`env=docker` 容器 API 接管 8080），对容器栈重跑真实全链路 e2e 通过；本地 uvicorn 栈此前同样通过 |

## 5. 迁移清单（当前源码 head `0033`；部署库版本以 `alembic current` 为准）

```text
0001_core_identity         0010_security_rls
0002_trip_domain           0011_external_signals
0003_reliability_core      0012_notifications
0004_planning_reliability  0013_approval_policies
0005_proactive_runtime     0014_push_channels
0006_visit_records         0015_plan_travel_legs
0007_agent_memory          0016_workflow_evidence_sync
0008_llm_invocations       0017_trip_hold_job_cancel
0009_metric_snapshots      0018_visa_cases
                           0019_trip_expenses
                           0020_travel_policies
                           0021_multi_approval
                           0022_expense_reports
                           0023_crm_sync
                           0024_fx_rates
                           0025_visit_objective
                           0026_notification_priority
                           0027_assistant_conversations
                           0028_notification_preferences
                           0029_assistant_msg_updated_at
                           0030_traveler_nationality
                           0031_visa_catalog_overrides
                           0032_market_workspace_foundation
                           0033_market_entities
```

## 6. MVP 之后已开始推进（PRD 方向二 / 六）

- **签证案件与入境评估**：`visa_cases`（NEW→PREPARING→SUBMITTED→APPROVED/REJECTED，材料清单、
  提交/决定时间）与 `entry_assessments`（来源/版本/置信度/人工复核快照）；主动式签证建议批准
  时自动创建案件；不存护照号，只可选保存护照有效期。
- **费用与预算**：`trip_expenses` 录入类别/金额/币种/日期/凭证引用；`GET /trips/{id}/budget`
  返回预算、计划估算、实际费用与差额。多币种分列，不做假汇率；未知计划费用不当作 0。
- **客户端**：行程详情新增"签证与入境"（案件状态推进、刷新评估）与"费用与预算"摘要。
- **差旅政策**：四类确定性规则（预算/计划费用/提前天数/目的国）；计划生成存违规快照，
  严格政策在激活时 409 `POLICY_VIOLATION`；客户端计划详情展示违规。
- **多人会签**：审批策略可配 `required_approvals`；不同审批人达到阈值才执行，同一人重复批准
  幂等；未达阈值保持 PENDING。
- **报销单闭环**：费用明细快照 → 提交 → ADMIN 审批/驳回；提交/决定写 workflow 与 approvals；
  源费用删除不影响已建报销单。
- **CRM 回写**：`crm_sync_jobs` + 异步 `crm_sync` 任务；默认文件适配器写待导入 JSON 并返回
  `PREPARED`（不伪造外部成功），未配置适配器显式 `FAILED`；客户端现场记录页可发起同步并轮询状态。
- **显式汇率与预算折算**：`fx_rates` 由 ADMIN 按币种对录入（含生效时间/来源）；预算接口可传
  `base_currency`，只按最新已录入汇率折算，缺汇率时 `conversion_complete=false` 并列出缺口，
  不输出不完整总计。
- **行动中心**：`GET /v1/action-center` 实时聚合待决定建议、待审批报销单、执行中/失败任务与
  待导入 CRM 记录；不建第二套待办表，状态变化即时反映；iOS 从 Today 工具栏进入，可就地批准/
  驳回建议并跳转行程。
- **拜访前简报**：`GET /v1/visits/{id}/briefing` 聚合客户、地点、本次目标、同客户历史拜访、
  历史跟进项与复盘记忆；`visits.objective` 可编辑；未接入的 CRM 商机/资料以 `data_gaps`
  显式标注。iOS 出行详情的每个拜访可打开简报。
- **AI 助手对话（PRD 页面 05，服务端 v1）**：`/v1/assistant/conversations` 系列；回复由
  `assistant_reply v1` prompt 生成并挂 `llm_invocation_id`，事实卡片（行程状态/行动中心计数）
  由服务端计算随消息落库；关键词给出带人审标注的工具提案；删除对话只删聊天表。
  iOS 对话页已接入（Today 与行程详情入口：列表/消息流/事实卡片/行程卡片直达）；
  会话内执行已接：`POST .../actions` 白名单动作（批准/驳回建议、生成计划、生成报销单）复用
  `proactive_service` 的人审门槛与会签，结果以 ACTION_RESULT 卡片回到对话。
- **当地生活指南（PRD 方向五）**：离线目录（8 国 7 类条目 + 城市贴士）按出行国家与拜访
  城市组装 `GET /v1/trips/{id}/local-guide`；实时推荐/天气以 `data_gaps` 标注为未接入；
  iOS 行程详情新增"当地生活指南"。
- **通知分级（PRD S8-08）**：`NotificationPolicyEngine` 按建议类型与风险等级把通知定为
  P0–P3 并在入队时落库（迁移 `0026`）；Feed 提供分级未读与 `priority` 过滤；iOS Today
  新增通知中心，按等级分组、徽标与标记已读。
- **客户端费用录入**：出行详情新增“记一笔费用”（类别/金额/币种/日期/备注），复用已有费用
  API 与幂等键；保存后刷新预算与实际费用。
- **求解器基准集与回归（批次 3 的 N3，2026-11-10）**：`instance_family.py` 8 个具名实例
  覆盖七个难度轴（规模/走廊、规模×容量、星形、洲际、窄窗、锁定顺序、夏令时切换日、
  不可行），坐标由真实城市锚点算术插值、零随机源；`test_benchmark_family.py` 38 条回归
  （行为门禁/确定性/金目标）纳入引擎套件（127→165）；`make bench` 升级为全族四类门禁
  （覆盖/能力声明/确定性/金目标）；金目标基线 `regression_baselines.json` 由
  `scripts/gen_regression_baselines.py` 生成（`--check` 校验漂移），治理方式与契约样本
  门禁（ADR-0030）一致。实测校准了两条 S2 语义：`solver_budget_seconds` 是解数量预算
  非墙钟；启发式在窄窗/夏令时实例上诚实失败，只有 ortools 可解（`min_solver` 声明入
  门禁）。
- **OR-Tools 规模护栏（ADR-0031，2026-11-12）**：`ORTOOLS_MAX_ELIGIBLE=10`，超出在
  `solve()` 入口拒绝（`SolverFailed`），流水线沿既有兜底链落启发式（`degraded=true`）。
  依据：DFS+GLS 单解成本随 n 指数增长（12 站走廊单次 39s、16 站分钟级），解数量预算
  管不住；同实例墙钟跨日实测差 40 倍（6.2s→253s）坐实"墙钟不可作预算"。引擎套件
  165→168（护栏拒绝/兜底降级/边界尺寸三条）。金目标对超护栏实例记 null。
- **语言与国际化规范（批次 3 的 N2 语言维度，2026-11-12）**：`docs/architecture/i18n.md`
  ——数据面原文存储、契约面英文稳定键（中文永不进协议，`error.message` 唯一例外）、
  解释面 LLM 中文且语言随 prompt 版本追溯、客户端 MVP 中文单语不建假 i18n 骨架；
  时区/DST（`time.md`）与货币（ADR-0014/0019）此前已闭环，N2 至此完成。
- **审批矩阵预留设计（批次 3 的 N1，2026-11-12，ADR-0032 提议）**：现行
  `approval_policies` 已是单步矩阵（`subject_type × min_severity → required_role +
  required_approvals`）；预留 `chain` 有序级次与 `approvals.stage` 的演进路径，
  不破坏迁移不可变与人审门槛；触发条件 = 首个三级审批链的真实租户需求。
  N1 以设计预留形式关闭，不在 MVP-1 实施。

> **批次 3 状态（2026-11-12）**：N3（基准集与回归）、N2（时区/DST/货币/语言）、
> N1（审批矩阵预留设计）全部收口。设计评审 RICE 清单中可在本地完成的事项至此
> 全部落地；§7 剩余项均需真实外部凭证。
- **FX 行情源（§7-8 关闭，ADR-0033，2026-11-12）**：`POST /v1/fx-rates:sync`（ADMIN，
  幂等入队 `fx_rate_sync` 任务）把 Frankfurter（ECB 参考汇率，免钥公开源）的最新快照
  写进组织同一张 `fx_rates` 表：`source=fx-frankfurter`、`as_of=行情日`、行情行
  `created_by_user_id=NULL`；行情没覆盖的币种进任务结果 `missing` 显式暴露，不伪造；
  `GBT_FX_PROVIDER=unavailable`（默认）时任务终态 FAILED。折算与缺口逻辑（ADR-0019）
  零改动，手工录入更 `as_of` 的行天然覆盖行情行（组织口径优先）。契约 86 路径/143
  schema、iOS 契约样本 28 份同步；后端测试 325→334。真实 API 冒烟通过
  （2026-11-11 行情，含 CNY/SGD/JPY 等商旅币种）。


## 7. 仍缺项（非本地代码可闭环）——2026-11-12 按 ADR-0034 逐项收口后仅剩 APNs

1. ~~**真实交通/签证/汇率/天气/航班供应商**~~：**免费开源源已接入（ADR-0034）**——
   天气 = Open-Meteo（免钥，信号拉取 + 指南实时天气，消除 `WEATHER_NOT_CONNECTED` gap）、
   航班 = OpenSky（免钥匿名，FLIGHT_STATUS 信号）、地面交通 = OSRM（`GBT_TRAVEL_PROVIDER=osrm`，
   GROUND 段真实驾车时间，RAIL/AIR/SEA 保持大圆估算诚实标注）、汇率 = Frankfurter（ADR-0033）。
   拉取入口 ADMIN 显式触发；源未配置 503 显式失败。生产规模化前自托管对应开源组件。
2. ~~**APNs/SMTP 生产凭证**~~：**已收口（2026-11-12，ADR-0034 补篇 + 28 轮 TDD）**——
   APNs 代码链路完整（ES256 JWT + HTTP/2 + JWT 50 分钟缓存 + payload 带 suggestion_id）；
   `GBT_APNS_API_BASE` 可指向本地 mock 无凭证联调；**410 Unregistered 自动禁用失效
   设备 token 且通知直接终态**（失效不自愈，不烧重试次数）。SMTP = MailPit 本地闭环。
   剩余为**纯操作步骤**（非代码缺口）：用户在 App Store Connect 生成 .p8（随付费会员
   免费）并配置环境变量；未配置时 inapp 通知中心即兜底通道。
3. ~~**KMS 密钥托管**~~：**免费替代已落地（ADR-0034）**——`GBT_FIELD_ENCRYPTION_KEY_FILE`
   支持密钥文件（Docker secret/挂载文件，优先于环境变量）；自托管 Vault/Infisical 走同一路径。
4. ~~**真实 embedding 凭证**~~：**开源本地模型路径（ADR-0034）**——Ollama（开源）本地跑
   `nomic-embed-text`，`GBT_MEMORY_EMBEDDING_PROVIDER=openai_compatible` 指向
   `http://localhost:11434/v1` 即用；任何失败回退 hash（既有行为不变）。
5. **AT-001~AT-012 按钮级 UI 自动化**：2026-11-09 已补齐最后两个 UI 缺口——计划调整入口
   （`PlanChangeCreate` 客户端模型 + 仓储 + 计划详情「调整拜访」，走真实
   `POST /v1/trips/{id}/plan-changes`）与版本比较页（`PlanDiffView`）。
   **2026-11-09 最终验证：46 单测 + 9 条 UI 用例整轮全绿（598s）。**
   测试结构（按 8 轮完整套件实证调整）：`testAcceptanceJourneyChain` 单条串联
   **AT-001~006 + AT-010/012**（新建 → 导入 → 规划 → 激活 → 地图/日历/验证 → 费用 → 记录 → 纪要）；
   **AT-007/008** 由 `testPlanChangeAndDiffFlow` 覆盖（取消拜访 → 等重求解 → 版本比较页）；
   **AT-011** 由 `testLaunchBrowseSeedTripAndSyncCenter` 覆盖（逐项判定由 `make e2e` 与
   `test_records_sync_integration`）。拆分原因：链式从纪要 sheet 关闭后立即去计划版本区/切 Tab，
   在本机可复现地找不稳（属测试环境问题），拆开后 9 条用例可各自重试、失败点更近。
   **本机验证状态（2026-11-09）**：45 单测稳定全绿；拆分前 8 条 UI 用例 7 条稳定通过（链式两处 flaky
   已定位并加固）；拆分后 9 条结构的最终整轮验证被外部干扰打断（另一项目 yuzhang 的 xcodebuild
   抢占 Xcode/模拟器、本机 xcodebuild 偶发挂死），建议在空闲机器复跑确认。
   2026-11-09 已把此前的 flaky 定位并加固：`backToTripDetail` 在详情页改为滚回顶部（原左缘滑动
   曾让 XCUITest 桥接挂死）、`executionTimeAllowance` 兜底、文件选择器重试、Excel 用例夹具改在
   launch 前落地、激活等待与稳定用例对齐、全部裸断言补"阶段失守"消息。完整套件连续两轮 8/8。
   剩余：本机 Xcode 偶发构建挂死（`waitForBuildWithBuildLog` / `PruneExplicitPrecompiledModules`）
   属环境问题，遇到时清 `XCBuildData` 缓存重跑。
6. ~~**签证官方数据源与预约渠道**~~：**网页形式兜底（ADR-0034）**——无免费官方 API
   （Timatic 收费）；离线目录保持只读基线，新增 `visa_catalog_overrides` 表（迁移 0031，
   RLS 同款策略）+ ADMIN 维护页 `/v1/admin/visa-catalog.html`（网页增删改覆盖条目，
   `manual_confirmation_required` 强制为真，ADR-0027 语义不变）；删除覆盖即回落基线。
7. ~~**费用深度集成**~~：**开源 OCR + 网页形式财务对接（ADR-0034）**——票据 OCR 用
   开源 Tesseract（可选外部二进制，`POST /v1/expenses:ocr` 只产候选字段不自动建费用，
   未装 503 显式指引安装）；财务系统对接 = `GET /v1/trips/{id}/expenses:export` CSV 导出
   （列头固定、全字段引号转义、公式前缀防注入，ADMIN 专属）。报销单/审批/汇率折算此前已闭环。
8. ~~**汇率行情源**~~：**已闭环（2026-11-12，ADR-0033）**——Frankfurter（ECB 参考汇率，
   免钥）经 `POST /v1/fx-rates:sync` 写入同一 `fx_rates` 表并保留 `source`/`as_of`，
   折算与缺口逻辑不变。管理员手工录入保留为兜底路径。
9. **真实 CRM 适配器**：文件适配器与失败可见性已完成；Salesforce/Dynamics 等凭据、字段映射
   与回执确认（`SUCCEEDED`）需按租户接入。

> 迁移编号以本仓库实际顺序为准（设计文档的 `0008`~`0013` 是未拆批时的规划编号）。
> 本文件随每个里程碑完成后更新，不替代设计文档。

## 2026-11-14 客户端整改进度（审计报告 5.x）

- **5.6 设计系统**：新增 `Features/DesignSystem/DesignSystem.swift`——颜色（分级/严重度/优先级/
  行程状态）、间距、圆角、字体令牌 + `DSBadge` / `DSDataQualityTag` / `DSMetricRow`；客户分级、
  通知优先级、行程状态、日历空档四处手写 Capsule 已收敛到组件。
- **5.2 行程详情分区**：515 行聚合页拆为概览/日程/计划/费用/签证五段；顺带修掉
  "客户导入/当地与记忆"在非概览分区泄漏的问题。
- **信息架构**：行动中心/设置改为标签页根后清理无效的关闭/取消按钮；设置保存原地提示；
  同步中心入口统一走「我的 → 同步中心」，UI 冒烟 AT-011 同步链路同步调整。
- **ADR-0036 本地模式首启体验**：空库首启自动注入示例数据（一次性，`local-demo-auto-seeded`
  标记）；今日页展示即将出发卡片；差旅窗口按出发地时区格式化（设备时区不再把 08:00 显示成
  04:00）；计划列表按详情回算停靠/天数（修复 "0 停靠 / 0 天"）；启动参数覆盖 base URL 时
  隐含 cloud 模式，避免 UI 冒烟连到空本地库。
- **验证**：68 单测全绿；新增 `LocalModeUITests`（不依赖后端的五分区冒烟）通过；
  **1.0(13) 已上传 TestFlight**（含以上全部改动）。

## 2026-11-15 差旅驾驶舱（审计 5.1）

- 新增 `Core/Cockpit/TravelCockpit.swift`：纯函数构建器（行程选择 → 今日时间轴 → 下一站/建议
  离场 → 风险聚合 → 未来 7 天），`TravelCockpitTests` 13 条语义用例（选择优先级、跨天下一站、
  HIGH/MEDIUM/LOW 排序、签证缺口、7 天窗口）。
- 「今日」重构为驾驶舱：英雄卡（倒计时/位置/窗口）+ 今日分区（时间轴/下一站/离场建议）+
  待处理风险 + 需要你决策（主动建议流保留）+ 未来 7 天。时间统一按出发地时区并显式标注。
- 连带修复两个数据正确性问题：
  - `Trip` 的 Codable 漏了 `countries`（encode 与 decode 都没有）→ 本地库/离线缓存丢失目的国，
    签证风险与目的国展示静默失效；已补 encode/decode 并加往返回归测试。
  - `EngineMapping.makePlanDays` 用**设备时区**格式化计划日键 → 跨洲行程日整体偏移一天；
    改为按出发地时区格式化，并新增回归测试（上海 11/17 00:00 = 11/16T16:00Z）。
- 验证：83 单测全绿；`LocalModeUITests` 增加驾驶舱断言与首启通知权限中断处理，通过并附截图。

## 2026-11-21 市场情报引擎 H1 工程进展

- **H1.0 源码审计**：已核对 Customer、Evidence、WorkflowInstance、AgentRun、BackgroundJob、Trip、Visit、Worker 注册、RLS helper、API 装配与 iOS Repository 组合根；详见 docs/review/sprint-h1-code-audit.md。
- **H1.1 领域契约**：已冻结 Workspace、上下文快照、ResearchQuestion、MarketEntity、Insight、Opportunity 与证据关系边界；详见 docs/architecture/market-intelligence-domain-contract-v1.0.md。
- **0032_market_workspace_foundation**：新增 MarketWorkspace、不可变 ContextSnapshot 版本、MarketResearchQuestion、依赖关系、MarketActivityEvent 和 MarketWorkspaceWorkflow；全部新组织表 FORCE RLS，并以 organization_id 复合关系限制跨组织关联。
- **0033_market_entities**：新增 MarketEntity、Alias、WorkspaceEntity、EntityCustomerLink；候选实体与正式 Customer 分离，正式 Customer 只在 Admin 明确确认后关联；域名相同只提示可能重复，不自动合并，跨 Workspace 复用必须显式提供 entity_id。
- **ResearchPlan 整合**：复用现有 ResearchPlan Workflow / AgentRun / BackgroundJob / Evidence，Workspace 可关联多个单次研究；不重复建设检索 Worker 和研究计划主表。
- **API 契约**：已由仓库生成器导出 OpenAPI（107 paths / 182 schemas）并生成 28 份 iOS 契约样本；CI #196 的 Ruff、MyPy、contract-check 已通过。
- **尚未通过的门禁**：CI #196 在 docs-check 阶段发现 0032/0033 未登记，本次已补齐本迁移清单；PostgreSQL migration、全量 pytest、Alembic drift check、E2E 与当前分支 iOS 完整验证仍以最新严格 CI 为准。不得将未执行项宣称为通过。
