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
| MS-6 | iOS 客户端 | ✅ | Today/行动中心/Trips/计划/地图/日历/现场记录/CRM/费用/简报/同步/记忆/设置；构建通过；17 单测 + 7 UI 自动化 |
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
| 出差结果分析与机会复盘（P1） | `trip_outcome_service` + `GET /trips/{id}/outcome` | 拜访结果计数、费用计划vs实际分列、纪要洞察按类计数、商机信号待确认、零 CRM 写入 |
| 纪要分类洞察（P1） | `meeting_minutes` v2 + `MUST_CONFIRM_KINDS` | 六类信息分类；非摘要强制人工确认（服务端覆盖模型）；模板兜底只回摘要；零 CRM 写入；iOS 待确认计数 |
| 入境评估结合证件/国籍（P0-7） | `traveler_nationality` + 迁移 `0030` | 画像登记/清除国籍（幂等、校验）；评估陈述证件前提、缺信息降置信度；不给确定性入境结论；租户隔离 |
| 客户多地址（P0-3） | `place_service.list/unlink_customer_place` + `routes/customers.py` | 一企业多地址增删查；主地址优先；来源+可信度随地址返回；解除关联不删地点；跨租户 404 |
| 自然语言创建出差（P0-2） | `trip_intake` v1 + `create_trip_draft` 动作 + 迁移 `0029` | 意图判定不误伤问状态；缺字段反问不编造；提案卡人确认；服务端二次校验 + 幂等；DRAFT 不预订 |
| 通知分级（S8-08 P0） | `notification_policy_service` + 迁移 `0026` | P0–P3 确定性规则入队落库；Feed `unread_by_priority`；`priority` 过滤；iOS 分组/徽标/已读 |

## 4. 验证记录

| 命令 | 结果 |
|---|---|
| `make test` | **235 passed**（真实 PostgreSQL） |
| `make typecheck` | MyPy：136 个源文件无问题 |
| `make lint` / `make check` | Ruff 通过；迁移无漂移 |
| `make contract-check` | 74 路径 / 116 schema；契约一致 |
| `make e2e` | 创建 → 导入 → 求解 → 激活 → 记录 → 同步 → 纪要 → 复盘 → 指标 → 审计，全部通过 |
| `make bench` | heuristic p50 0.4ms / p95 0.5ms；ortools p50 0.3ms / p95 0.4ms；6/6 覆盖、hash 稳定 |
| `make ios-build` | BUILD SUCCEEDED |
| `make ios-test` | 17 单测 + 7 UI 自动化通过（连接真实后端） |
| `swiftlint lint` | 0 violations（配置见 `.swiftlint.yml`） |
| `make stack-up` + E2E | Docker DB/API/Worker 健康，真实全链路通过 |

## 5. 迁移清单（当前 head `0030`）

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


## 7. 仍缺项（非本地代码可闭环）

1. **真实交通/签证/汇率/天气/航班供应商**：Provider/信号接口与降级已就绪，鉴权、字段映射与
   服务商账号需接入。
2. **APNs/SMTP 生产凭证**：代码与设备 token 管理已完成；需要 `.p8`/Team ID/Topic 或 SMTP 账号。
3. **KMS 密钥托管**：字段加密与轮换脚本已就绪；key 仍来自环境变量。
4. **真实 embedding 凭证**：hash/OpenAI-compatible provider 已就绪，需配置模型与 key。
5. **AT-001~AT-012 按钮级 UI 自动化**：已覆盖启动、Today、行动中心、新建出行、列表、详情、
   拜访简报、费用录入、计划生成/激活、现场记录提交、会议纪要生成、Excel 选择器导入与同步页
   （7 条 XCUITest，见 `docs/acceptance-tests/ios-ui-automation.md`）；尚无单条脚本串起全部
   12 个 AT 场景。
6. **签证官方数据源与预约渠道**：当前用离线目录做要求建模，真实官方核验/预约未接入。
7. **费用深度集成**：报销单快照、审批与显式汇率折算已闭环；实时汇率源、票据 OCR 与财务系统
   对接未接入。
8. **汇率行情源**：当前只接受管理员手工录入；公开 API 接入后应写入同一 `fx_rates` 表并保留
   `source`/`as_of`，折算与缺口逻辑不变。
9. **真实 CRM 适配器**：文件适配器与失败可见性已完成；Salesforce/Dynamics 等凭据、字段映射
   与回执确认（`SUCCEEDED`）需按租户接入。

> 迁移编号以本仓库实际顺序为准（设计文档的 `0008`~`0013` 是未拆批时的规划编号）。
> 本文件随每个里程碑完成后更新，不替代设计文档。
