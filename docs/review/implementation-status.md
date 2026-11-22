# 工程实施进度核对（PRD MVP-1 基线 / 设计文档 v2.0）

- 核对时间：2026-11-22（市场情报 H1.9 增量更新）
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

## 5. 迁移清单（当前源码 head `0047`；部署库版本以 `alembic current` 为准）

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
                           0034_market_insights_evidence_versions
                           0035_market_opportunities
                           0036_market_research_plan_evidence
                           0037_market_question_answers
                            0038_market_evidence_derivations
                           0039_market_claim_verification
                           0040_market_research_graph
                           0041_market_question_evidence_scope
                           0042_market_claim_lineage
                           0043_market_opportunity_visit_links
                           0044_visit_follow_up_lifecycle
                           0045_receipt_assets
                           0046_report_receipt_snapshot
                           0047_receipt_ocr
```

- **拜访跟进闭环（H2.12）**：`visit_follow_ups` 保存经用户逐项 ACCEPT/DISMISS 的现场记录或会议纪要任务，以及用户手工创建的跟进项；`visit_follow_up_events` 追加记录确认、驳回、启动、完成、重开与修改。来源校验限定在同一组织/拜访；行动中心聚合 OPEN/IN_PROGRESS 与近 7 天 COMPLETED。强制租户 RLS；不递增 Trip 计划版本、不自动更新 CRM、不发送客户消息。API：`GET/POST /visits/{id}/follow-ups`、`PATCH /visit-follow-ups/{id}`。

- **H2.13 票据资产与报销证据快照（进行中）**：新增私有 ReceiptAsset 元数据、原始文件私有存储适配器（本地原子文件存储 / S3 兼容 Bucket）、组织与 Trip 授权的上传/下载 API、签名嗅探与大小限制、SHA-256 和幂等重放；TripExpense 通过同组织/同 Trip 复合外键关联票据。新增迁移 `0045_receipt_assets`。迁移 `0046_report_receipt_snapshot` 为报销明细固定票据 Asset ID、内容 SHA-256、原始文件名、MIME 与字节数；复合外键保护相同组织/行程，延迟 NO ACTION 约束防止删除仍被报销快照引用的原件。原始二进制不进入 PostgreSQL，storage key 不暴露给客户端；Docker API/Worker 共用持久卷 `gbt_os_receipt_data`，重建容器不会清空本地票据对象。新增迁移 `0047_receipt_ocr`：对已保存的图片 ReceiptAsset 执行 Tesseract OCR，保存 content hash、引擎版本、候选字段与 AgentRun 关联；不复制原始票据文本，不因识别结果创建/修改费用。用户可对一次 OCR 运行单独确认或驳回，确认字段写入只追加审阅记录，数据库撤销应用角色 UPDATE/DELETE。PDF 仍可保留与下载，但当前 OCR 明确只支持图片格式。仍待完成对象存储孤儿对账、Trip 结账就绪度与 iOS 离线票据入口；CI/真实数据库验收以本分支最新 Actions 为准。

- **H2.13 Trip 结账就绪度（进行中）**：新增只读 `GET /v1/trips/{trip_id}/closeout-readiness`，对拜访结果、费用报销覆盖、票据原件与 OCR 人工确认、报销单状态进行证据化评估；以币种分别输出金额，不合并未知汇率。未完成拜访、缺少已生效拜访结果、未进入报销快照、最新报销单驳回属于硬阻塞；缺票据、OCR 尚未确认、多币种复核与未完成商务跟进属于警告/例外，商务跟进不阻断财务结账。评估不修改 Trip/Visit/Expense/Report、不递增 Trip 计划版本；Action Center 仅聚合硬阻塞，并回链到 Trip 与来源对象。

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


## 10. 市场情报引擎 H1.3 — Evidence 版本化与 MarketInsight

> 本节记录 `feat/sprint-h1.3-evidence-insights` 叠加分支中的工程实现；不覆盖上文原有 Trip MVP 验收记录。

| 能力 | 实现 | 约束 |
|---|---|---|
| Evidence 兼容扩展 | 迁移 0034 为现有 evidence 增加 published_at、canonical_ref、content_hash、observation、verification_status、quality | 新字段允许 NULL；旧 record_many() 与原唯一约束保持不变 |
| 来源内容版本化 | 新市场来源按规范化来源标识 + 原文 SHA-256 生成 version ref | 同来源同内容幂等；同来源内容改变生成新行；不同来源不会因内容相同被合并 |
| MarketInsight | 组织级、Workspace 级版本化判断记录 | insight_key + version 唯一；生成来源 AgentRun 必须同组织；修订追加新版本 |
| InsightEvidence | 显式 SUPPORTS / CONTRADICTS / CONTEXT 关联 | 同组织复合外键；Evidence 删除受保护；不使用多态 target_id |
| 人工审核 | ACCEPTED / REJECTED 必须由用户动作记录 reviewer 和时间 | 采纳必须至少有一条支持证据，且不能有未解决反驳；同一 insight_key 同时只允许一个 ACCEPTED 版本 |
| 证据不足 | 可通过 insufficient_evidence_reason 明确记录信息缺口 | 不允许将无证据判断采纳为已确认事实 |
| 活动时间线 | 首次保存证据版本、Insight 创建/采纳/驳回追加 MarketActivityEvent | 幂等证据重放不重复产生事件；不提供普通修改/删除事件的 API |

新增 API：
- POST/GET /v1/market/workspaces/{workspace_id}/evidence
- POST/GET /v1/market/workspaces/{workspace_id}/insights
- POST .../insights/{insight_id}:accept
- POST .../insights/{insight_id}:reject

本节代码仍需在 PR #4 分支通过完整 CI 与 PostgreSQL 集成测试后，才能标记为验证完成。当前未声称 E2E、iOS UI 或生产数据库迁移已完成。MarketOpportunity、Trip 关联、iOS Workspace 原生 UI 与 Agent 自动化仍在后续切片。


## 11. 市场情报引擎 H1.4 — Opportunity 与 Trip 行动关联

> 本节记录 `feat/sprint-h1.4-opportunities-trip-links` 叠加分支的实现状态；以 PR #5 最新 CI 为最终验收依据。

| 能力 | 实现 | 约束 |
|---|---|---|
| MarketOpportunity | Workspace 级机会候选、负责人、当前阶段及下一步动作 | 动态行业/机会类型；不替代 Customer 主档 |
| Assessment 历史 | 每次评估写入 MarketOpportunityAssessment 不可变快照，Opportunity 行只保存当前投影 | 每维必须有 value 与 rationale；UNKNOWN 明确保留；证据引用必须属于该 Opportunity；不自动合成成交概率 |
| 机会证据 | MarketOpportunityEvidence 显式 SUPPORTS / CONTRADICTS / CONTEXT | 必须引用当前 Workspace 或其 ResearchPlan Evidence；Evidence 删除受保护 |
| 候选实体关联 | MarketOpportunityEntity 关联当前 Workspace 已纳入的 MarketEntity | 不能跨 Workspace 任意挂实体，不修改 Customer 主档 |
| 人工资格确认 | QUALIFICATION_PENDING → QUALIFIED | 必须有支持证据、无未解决反驳，并记录确认人/时间/理由 |
| 结果闭环 | ACTION_PLANNED → WON / LOST | 只允许人工记录且必须提供实际业务结果说明；DISQUALIFIED 必须提供理由 |
| Workspace–Trip | MarketWorkspaceTripLink 多对多关联现有 Trip，允许解除和重新关联 | 复合组织外键；解除保留审计历史；Workspace 删除不级联删除 Trip |
| 活动时间线 | 阶段变化、评估、机会证据/实体关联、Trip 关联/解除均写 MarketActivityEvent | 领域活动事件不替代 Outbox |

新增 API 包括机会列表/创建/详情、证据关联、候选实体关联、评估、状态迁移、人工资格确认、结果记录，以及 Workspace Trip 列表/关联/解除。

H1.4 当前代码 head 已通过 Backend 严格门禁：Ruff、MyPy、OpenAPI、文档、0035 PostgreSQL migration、Alembic drift 和全量 Backend pytest（416 passed / 1 skipped）。SwiftLint 与 Xcode Build 也已通过；iOS Unit Tests 正在对最新清理提交重新执行。PR #5 保持 Draft，尚未合并或部署。原生 iOS Opportunity UI 尚未实现。


## 12. 市场情报引擎 H1.5 — Research Worker 版本化证据闭环

新增迁移 `0036_market_research_plan_evidence.py`（revision `0036_market_plan_ev`，父 revision `0035_market_opps`），不新增 Evidence 主表、不新增搜索 Provider 或 Worker。

| 能力 | 实现 | 约束 |
|---|---|---|
| Worker 版本化落证据 | Workspace 关联的 ResearchPlan Worker 将搜索标题/摘要写入既有 Evidence，并调用既有来源规范化与 SHA-256 版本键 | source + content 变化产生新 Evidence；同源同内容复用已有 Evidence |
| ResearchPlan 来源归属 | MarketResearchPlanEvidence 关联 Workspace、ResearchPlan Workflow、Evidence、采集 AgentRun、搜索查询与 query SHA-256 | 复合外键跨租户保护；ResearchPlan 必须已关联当前 Workspace |
| 多查询归因 | 同一结果被多条已审阅 query 命中时，一条 Evidence 可以关联多个 query | 唯一键使相同 query 关联幂等 |
| ResearchPlan 详情 | 详情接口合并传统 ResearchPlan subject Evidence 与 Workspace 版本化 Evidence | 旧独立 ResearchPlan 保留原兼容读取路径 |
| 未核验边界 | SearXNG 搜索结果固定写为 UNVERIFIED，保留 snippet、发布日期原值、Provider、Engine、query 与 AgentRun | 不把搜索摘要伪装为全文或已核验事实 |
| 活动审计 | 新 Evidence 版本记录 MARKET_EVIDENCE_RECORDED；每个新 Plan/query 关联记录 MARKET_RESEARCH_EVIDENCE_LINKED | 同内容重放不重复创建 Evidence 事件或关系事件 |

新增集成验收覆盖：相同 Workspace、相同 URL、相同内容跨 ResearchPlan 只形成一条 Evidence；查询归属分别保存；同 URL 内容变化追加新版本；ResearchPlan 详情和 Workspace Evidence 列表均可读取；未核验状态不被抬高。

该切片仍不负责自动抓取全文、事实核验、LLM 综合结论或自动采纳 Insight；这些需要后续 Research Agent/Verification Agent 阶段。


## 13. 市场情报引擎 H1.6 — ResearchQuestion 答案版本与证据审阅

新增迁移 `0037_market_question_answers.py`（revision `0037_market_q_answers`，父 revision `0036_market_plan_ev`），增加：
- MarketResearchQuestionAnswer：每个问题的不可变答案版本、假设、未知项、置信度、证据缺口、来源 AgentRun 与审核记录；
- MarketResearchQuestionAnswerEvidence：明确 SUPPORTS / CONTRADICTS / CONTEXT 关系，引用同组织 Evidence；
- 人工审核接口：答案默认 NEEDS_REVIEW；支持、反驳并存标记 CONFLICTED；只有人工 Admin 接受后，ResearchQuestion 才变为 ANSWERED；
- 版本修订追加写入；新版本被采纳时旧 ACCEPTED 版本转 SUPERSEDED，同一问题至多一个 ACCEPTED；
- acceptance_criteria 中的 independent_sources 在采纳前由后端检查独立来源 host 数量；其它未实现自动判定的标准要求审核说明，不伪装成机器验证；
- 答案 Evidence 只允许来自当前 Workspace 或其关联 ResearchPlan；Service 校验 Workspace 来源范围，数据库复合外键确保答案/问题的组织与 Workspace 归属，以及 Evidence 同组织；
- 创建、采纳、驳回事件进入 MarketActivityEvent。

新增 API：
- POST/GET /v1/market/workspaces/{workspace_id}/questions/{question_id}/answers
- POST .../answers/{answer_id}:accept
- POST .../answers/{answer_id}:reject

该切片只建立证据驱动、可审核的答案工作流；尚未实现 LLM 自动回答/事实核验 Agent，也不自动将 ResearchPlan 产出的摘要采纳为答案。需要最新 CI 的 PostgreSQL 迁移、Alembic drift、全量 Backend pytest 和 iOS 门禁通过后才能验收。


## 14. 市场情报引擎 H1.7 — Research Answer Agent

新增异步答案草拟 Agent，完整复用现有组件：
- API：`POST /v1/market/workspaces/{workspace_id}/questions/{question_id}:draft-answer`；
- BackgroundJob job_type：`market_question_answer_draft`，注册于现有 `KNOWN_JOB_TYPES` 与 `TASK_HANDLERS`；
- WorkflowInstance workflow_type：`market_question_answer`，通过 MarketWorkspaceWorkflow 归属 Workspace；
- AgentRun 记录输入证据 ID 快照、Prompt 版本和最终答案版本；
- 唯一模型入口为既有 LLMGateway；输出有版本化 Prompt/Schema，并记录 LLMInvocation；
- 默认最多选择 24 条当前 Workspace 或关联 ResearchPlan Evidence；支持用户显式限定 evidence_ids；
- 模型只能引用输入证据快照中的 ID，越权/虚构引用由服务端排除；
- 未核验来源不能输出 HIGH confidence；缺少 SUPPORTS 时置信度降为 INSUFFICIENT；
- LLM 降级、输出不可用或无来源内容时产生明确 evidence_gap，不编造事实；
- 生成的答案版本始终 NEEDS_REVIEW / CONFLICTED，不自动采纳，不把问题标记为 ANSWERED；
- 相同问题、Prompt 版本、Evidence 快照的在途任务使用 dedupe key 复用。

新增 PostgreSQL 集成测试覆盖任务排队与去重、LLM Gateway 输入契约、来源 ID 白名单、未核验来源置信度上限、AgentRun/LLMInvocation 追溯、问题不被自动采纳及无证据时的显式缺口。

H1.7 不新增迁移和 Evidence 主表；也未实现全文网页抓取、独立来源核验 Agent、Research Plan 自动扩展或 iOS 原生 Agent 状态/审核界面。必须在本分支完整 CI 通过后才可验收。


## 15. 市场情报引擎 H1.8 — 用户选定来源的全文提取与派生证据链

H1.8 在 H1.7 Research Answer Agent 的基础上补齐一段严格受控的“搜索摘要 → 原始网页提取”链路，但**不把网页抓取视为事实核验**。

- **用户意图边界**：只有用户明确对 Workspace 中某条 `WEB_SEARCH_RESULT` 调用 `POST /v1/market/workspaces/{workspace_id}/evidence/{evidence_id}:extract`，才创建异步任务。搜索 Worker 不会自动抓取所有结果；其他 Evidence 类型、被驳回来源、跨 Workspace 来源均不能触发。
- **安全 Provider**：新增 `SafeHttpResearchPageProvider`，默认 `unavailable`（关闭）。显式设置 `GBT_RESEARCH_PAGE_PROVIDER=safe_http` 后才开放；只允许 HTTP 80 / HTTPS 443，禁止 URL 凭证；每一跳重定向都重新校验 URL 与 DNS；DNS 返回的全部地址都必须是公网地址，socket 直接连接已校验 IP，TLS 仍用原域名验证证书/SNI；最多 3 次重定向并禁止 HTTPS 降级；最大读取 2 MiB（配置上限 4 MiB）、只接受 HTML/XHTML/纯文本、10 秒默认超时。
- **数据留存**：不保存整页 HTML 或全文副本。提取后仅保存受限正文摘录（默认 12,000 字符，上限 20,000）以及规范化全文的 SHA-256 和字符数；来源元数据、抓取时间、最终 URL、提取器版本独立保留。全文哈希不是内容正确性证明。
- **版本与血缘**：新增 `MarketEvidenceDerivation` / 迁移 `0038_market_evidence_derivations`，通过 organization + workspace + workflow + AgentRun + source Evidence + derived Evidence 复合外键保存只追加的派生关系；FORCE RLS；应用角色不能 UPDATE/DELETE/TRUNCATE。
- **执行可靠性**：复用既有 WorkflowInstance、MarketWorkspaceWorkflow、BackgroundJob、AgentRun、TASK_HANDLERS、Job retry/DLQ。成功、失败、重试耗尽进入 MarketActivityEvent；API 可读取任务状态；相同来源内容与提取器版本使用 dedupe key。
- **事实状态不升级**：抓取结果固定写入新的 `WEB_PAGE_EXTRACT` Evidence，`verification_status=UNVERIFIED`；原始搜索摘要 Evidence 不被覆盖。后续独立来源比对与事实核验属于 H1.9，不在 H1.8 的完成范围。
- **新增测试**：纯离线 Provider 测试覆盖 URL/DNS/重定向 SSRF、安全端口、HTTP 429 可重试、404 终态、媒体类型/响应大小、隐藏内容剔除、正文截断与全文哈希；PostgreSQL 集成测试覆盖用户触发、Evidence 原文保留、异步任务执行、派生关系及状态读取。

**当前验证限制**：本次代码已提交到 `feat/sprint-h1.8-source-page-extraction`，新增数据库迁移 `0038`、API、Worker 与测试；还未在当前执行环境运行 Python/真实 PostgreSQL，也未完成本分支 GitHub Actions。不得将本切片标记为已通过 CI 或已部署。生产环境默认关闭网页抓取 Provider。


## 17. 市场情报 H1.9 — Claim-level Verification Agent

H1.9 在 H1.8 的 Evidence 版本和 H1.7 的答案版本上构建原子主张核验，不把“LLM 认为有支持”当作事实认证。

- **显式运行边界**：用户对某一答案版本显式触发 `POST /v1/market/workspaces/{workspace_id}/answers/{answer_id}:verify-claims`。任务只读取该答案版本已关联的 Evidence 快照；不会自动搜索、抓取或扩展来源范围。
- **领域对象**：新增 `MarketClaimVerificationRun`、`MarketClaim`、`MarketClaimEvidence` 与迁移 `0039_market_claim_verification`。保存答案版本、Workflow、AgentRun、输入摘要、原子主张版本、每条主张与 Evidence 的关系、来源 host/content hash/verification status 快照。
- **Agent 职责**：拆分 FACT / ESTIMATE / OPINION / FORECAST / INFERENCE；逐条输出 SUPPORTS / CONTRADICTS / CONTEXT；必须引用输入 Evidence ID。服务端丢弃虚构 ID、重复 ID、非法关系和空白主张。
- **确定性重算**：程序重新计算 PROVISIONAL_SUPPORT / POTENTIAL_CONTRADICTION / CONFLICTED / INSUFFICIENT，不直接采用模型置信度。只出现支持关系也仅是待审核的暂定支持。
- **来源独立性**：记录精确 host、内容哈希、来源核验状态；同 host / 重复 hash 会标注；不同 host 只叫 DISTINCT_HOST_UNCONFIRMED。`confirmed_independent_source_count` 数据库约束固定为 0，Agent 不得自动声明来源独立。并同步修正 H1.6：答案级 `independent_sources` 先按 host + 内容指纹聚类为候选来源组，达到数量仍必须管理员人工核查出版方所有权/转载关系并填写审核说明；不会自动通过独立性。
- **置信上限**：不输出 HIGH。默认 Evidence 未核验时为 LOW；只有所有支持 Evidence 已 VERIFIED 且存在多个 host 时才允许 MEDIUM，但仍明确说明 host 不等于独立出版方。
- **人工审核**：管理员可采纳/驳回单条主张；只有没有反驳且有 SUPPORTS 关系的 PROVISIONAL_SUPPORT 可进入人工采纳。数据库 CHECK 与列级权限共同禁止直接修改主张正文、范围和证据映射；接受状态必须有 reviewer/time/reason 且无反驳支持。采纳状态不等于 VERIFIED，也不改写原答案版本或 Evidence 状态。
- **数据库不可变性**：`gbt_app` 对 `market_claims` 只能 UPDATE 审核状态、人、时间、理由列；主张正文/范围/类型/核验结论/计数不可改。CHECK 约束要求 ACCEPTED 主张必须为 PROVISIONAL_SUPPORT、至少一条 SUPPORTS、零 CONTRADICTS，并具备非空审核理由；只追加 Evidence 关系仍禁止 UPDATE/DELETE/TRUNCATE。
- **可靠性**：复用 BackgroundJob、WorkflowInstance、MarketWorkspaceWorkflow、AgentRun、LLMInvocation、幂等入队与 retry/DLQ；同一答案/提示版本/证据内容快照可复用完成的结果。
- **新增验收测试**：覆盖支持与反驳冲突、不同 host 不能证明独立、UNVERIFIED 不升级、虚构 Evidence ID 排除、人工审核与原答案状态不变、无 Evidence 不可启动。

**当前验证状态**：代码与迁移已提交至 `feat/sprint-h1.9-claim-verification-agent`，尚未完成最新 GitHub Actions；在 CI 全绿前不得声称 H1.9 已验收或迁移已部署。


## 2026-11-22 市场情报引擎 H2.0 — Workspace context-aware Research DAG

- 在现有 ResearchPlan Planner 输入中加入 Workspace 名称、目标和当前采纳的 ContextSnapshot；按 Workspace 行锁读取，确保规划输入与上下文版本一致。`EXTERNAL_ONLY` 仍然禁止读取 Trip / Customer 内部记录，但不丢弃用户主动提供的 Workspace 研究背景。
- Planner context 不在 AgentRun 通用输入日志中复制原文；持久化 snapshot ID、版本和 SHA-256，并将上下文来源摘要记入 ResearchPlan Evidence。上下文指针无对应当前快照时，拒绝启动规划。
- Research Planner Prompt 升级到 v2，结构化输出研究图节点（问题、优先级、验收标准、预期证据和依赖 key）。服务端对 LLM 输出进行数量、类型、key、边引用和 DAG 校验；循环边不写入数据库，并显式返回 planning warning。
- 研究图节点物化为现有 `MarketResearchQuestion`，使用 `source=AGENT`、`generated_by_run_id` 和 `planner_question_key` 追溯生成运行；依赖写入现有 `MarketQuestionDependency`，不把依赖混同为 parent/child 层级。
- 新增 `0040_market_research_graph`：为问题节点增加预期证据和 Planner 运行来源字段、同组织复合外键、节点 key 唯一约束及来源字段成对约束。
- 新增 `GET /v1/market/workspaces/{workspace_id}/research-graph`，返回拓扑顺序、依赖边、就绪问题和阻塞状态；新增依赖 CRUD，服务端以 Workspace 行锁串行化写入并拒绝自依赖、跨 Workspace 引用和循环依赖。
- **验证状态：** 已提交到 `feat/sprint-h2.0-research-dag-orchestrator`；真实 PostgreSQL 集成用例、OpenAPI / iOS 契约、Alembic drift、全量 Backend pytest 和 iOS 门禁须以该分支最新 CI 为准。当前不声称 H2.0 已验收或已部署。

## 2026-11-22 市场情报引擎 H2.9 — Claim Impact Intelligence

- 新增只读 `GET /v1/market/workspaces/{workspace_id}/claim-impact-report`，通过 `MarketClaimEvidence` 与 `MarketInsightEvidence` / `MarketOpportunityEvidence` 的精确 Evidence ID 关联识别可能受影响的下游市场判断与商业机会。
- 最新主张仍待审核、存在冲突/证据不足时，报告提示关联消费者复核；新主张已采纳但消费者仍引用不在最新版本内的旧 Evidence 时，标记旧证据快照。
- 报告不修改任何 Insight / Opportunity / Claim 状态，不推进或回退商机阶段，不自动撤销人工决策；不使用语义相似度推断依赖。
- 查询按 organization + Workspace 隔离，并设置 200 条主张谱系、5000 条历史主张和 250 条影响关系的上限；截断时返回标记。
- **验证状态：** 代码和集成测试已提交至 `feat/sprint-h2.9-claim-impact-intelligence`；最新 GitHub Actions 为最终验收依据。该分支保持 Draft PR，不代表已合并或已部署。
