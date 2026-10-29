# 扩展验收映射（MS-5 收尾 / 记忆 / LLM / 指标 / iOS）

本文件补充 `proactive-runtime.md`，覆盖 AT-010~AT-012、M2/M3/M6 与客户端构建验证。
所有后端用例打真实 PostgreSQL（`zhi-tu-pg`），不使用模拟 API 或预置演示数据。

```bash
make db-up
make test
make contract-check
make bench
make api        # 另开一个终端
make e2e
make ios-build
make ios-test
```

## 场景映射

| 编号 / 能力 | 测试文件 | 关键断言 |
|---|---|---|
| AT-010 现场记录（可离线保存） | `services/api/tests/test_records_sync_integration.py::test_record_write_is_idempotent_and_advances_visit` | `client_record_id` 重放不产生第二条记录；拜访状态推进为 `DONE`；换幂等键重试仍幂等 |
| 冲突矩阵（拜访记录 LWW） | 同文件 `test_last_writer_wins_keeps_losing_copy` | 较旧写入 `applied=false` 留档；服务端当前值不变；新写入版本号递增 |
| AT-011 网络恢复同步 | 同文件 `test_sync_push_returns_per_item_verdicts_and_is_idempotent` | 逐项 `applied/rejected`；计划停靠 `PLAN_IMMUTABLE`；缺 `visit_id` 拒绝；整批同键重放字节一致 |
| AT-S01 记录写隔离 | 同文件 `test_cross_tenant_cannot_write_record` | 组织 B 写组织 A 拜访 → 404 |
| AT-012 会议纪要草稿 | `services/api/tests/test_llm_integration.py::test_minutes_draft_records_invocation_and_replays_from_cache` | 草稿含跟进任务；每次草稿可回溯 `llm_invocations`；同输入缓存命中 `cached=true` |
| M3 降级 | 同文件 `test_gateway_degrades_to_fallback_when_provider_fails` | provider 故障 → 模板兜底、`degraded=true`、`error` 保留原因 |
| M3 工具契约 | 同文件 `test_tool_catalog_marks_approval` | `visit.draft_minutes` 标注 `requires_approval=true` |
| M2 记忆与复盘 | `services/api/tests/test_memory_integration.py` | 复盘摘要含决策/待办/教训；重复复盘幂等；过程记录随行程返回；画像版本生效；混合检索命中；跨租户 404 |
| M6 指标树 | `services/api/tests/test_metrics_integration.py::test_metric_tree_computes_from_real_rows` | 9 项指标从真实行计算，比率与分母正确 |
| M6 快照 | 同文件 `test_snapshot_is_idempotent_per_window` | 同窗口重跑覆盖，不堆重复行 |
| M5 全库 RLS 兜底 | 同文件 `test_all_org_tables_force_rls` | 所有带 `organization_id` 的表均 `ENABLE` + `FORCE`（`memberships` 按 ADR-0002 例外） |
| MS-6 iOS 构建/契约 | `GlobalTravelOSTests/ModelDecodingTests.swift` | Trip 扁平窗口、计划 stops 分组、主动建议 Feed、逐项同步结果、记忆、纪要、离线队列 `visit_id` |
| EXTERNAL 触发源 | `services/api/tests/test_external_signals_integration.py` | 外部信号 → `VISA_POLICY_UPDATE` 建议；触发记录 `type=EXTERNAL`；同键重推幂等；内容变化在冷却期内被抑制；跨租户不可见 |
| 推送通道 | `services/api/tests/test_notifications_integration.py` | 已推送建议生成通知；Outbox 重投只有 1 条；Worker 投递后 `SENT`；已读幂等；保留建议不通知 |
| S3 可观测性 | `services/api/tests/test_observability_integration.py` | JSON 日志含 trace/org/trip/usecase/latency；格式器白名单；按 Trip 的审计时间线 |
| 覆盖不变量 | `packages/planning-engine/tests/test_pipeline.py` | eligible 停靠未排入 ⇒ `SolverFailed`，不再产出 0 停靠可行计划（ADR-0007） |
| MS-7 服务端全链路 | `make e2e`（`scripts/acceptance_e2e.py`） | 创建 → 导入 → 求解 → 激活 → 记录 → 同步 → 纪要 → 复盘 → 指标 → 审计，全部真实 HTTP/DB/Worker |
| MS-7 客户端 UI 自动化 | `GlobalTravelOSUITests`（7 条） | 启动 → Today → 通知中心 → 行动中心 → 列表 → 详情 → 同步页；新建出行；拜访简报；费用录入；计划生成/激活；现场记录提交；Excel 选择器导入，连接真实后端 |
| 计划契约 | `services/api/tests/test_plan_contract.py` | 嵌套详情路径 `/trips/{id}/plans/{plan_id}`；列表 `stop_count/day_count`；错误出行 404 |
| N1 审批策略 | `services/api/tests/test_approval_policy.py` | 高风险建议要求 ADMIN；TRAVELER 批准 403；ADMIN 通过 |
| PII 加密/留存 | `services/api/tests/test_retention_pii.py` | Fernet 往返 + 旧明文兼容；过期笔记/记忆清空，最近记录不动 |
| iOS 导入契约 | `ImportModels` + `ImportSheet` | 两阶段预览/勾选/确认；模型按后端 `VALID/INVALID/DUPLICATE` 解码 |
| 事务时序 | `services/api/tests/test_transaction_middleware.py` | 2xx 响应开始前提交；4xx 回滚；提交失败替换为 500 |
| 推送通道 | `services/api/tests/test_push_channels.py` | 设备 token 幂等注册/注销；SMTP 收件人；APNs ES256 JWT（raw r‖s）；未配置显式失败 |
| 密钥轮换 | `services/api/tests/test_key_rotation.py` | 旧 key 密文重加密；明文默认跳过；不可解密文不二次加密 |
| Docker 栈 | `make stack-up` + `make e2e` | API/Worker/DB 容器健康，真实全链路通过 |
| 规划状态/交通段/费用 | `packages/planning-engine/tests/test_pipeline.py`、E2E | `solution_status`、legs 与 stops 对齐、未知费用为 NULL |
| AT-003 Provider 降级 | `services/api/tests/test_travel_provider.py` | Provider 失败 ⇒ 重试 + fallback，全部段 stale、费用未知 |
| 计划变更 | `services/api/tests/test_plan_changes.py` | 基础版本校验、WorkflowInstance、改输入并入队；旧版本 409 |
| Trip 状态机 / 任务取消 | `services/api/tests/test_prd_workflow_extra.py` | hold/resume/complete、PENDING 可取消、RUNNING 409 |
| 工作流/证据/同步/动作 | 同上 | evidence/external_action/sync_operation/agent_run/integration_connection 实际落库 |
| iOS Map / Calendar | `PlanMapView`、`PlanCalendarView` + `make ios-build` | 计划停靠有坐标时地图可渲染；日历按日展示 |
| 离线时间 / 重启恢复 | `OfflineIndicator`、`AppSettings.lastTripId` | 离线摘要显示更新时间；出行页显示"上次出行" |
| CI 门禁 | `scripts/ci.sh`、`.github/workflows/ci.yml` | Ruff/MyPy/Pytest/迁移/契约/E2E/iOS/SwiftLint |
| 签证案件（后 MVP） | `services/api/tests/test_visa_cases.py` | 国家案件创建、状态机推进/非法推进 409、入境评估来源与人工复核、批准签证建议自动建案 |
| 费用与预算（后 MVP） | `services/api/tests/test_expenses.py` | 多币种分列、同币种差额、计划费用未知计数、跨租户 404 |
| iOS 签证/预算 UI | `VisaCase.swift`、`BudgetSummary.swift`、TripDetail | 案件状态推进、刷新评估、预算/实际/差额展示 |
| 差旅政策 | `services/api/tests/test_policies.py` | CRUD、预算/费用/提前天数/目的国违规、严格政策激活 409 |
| iOS 政策展示 | `PolicyViolation` + PlanDetail | 计划详情展示违规码与说明 |
| 多人会签 | `services/api/tests/test_approval_policy.py` | 2 人阈值：首人 PENDING、同一人重批不计数、第二人 EXECUTED |
| 报销单 | `services/api/tests/test_expense_reports.py` | 快照明细、多币种合计、提交/审批/驳回、源费用删除不影响快照、跨租户 404 |
| iOS 报销单 UI | `ExpenseReport.swift` + TripDetail | 建单、提交、状态与分币种合计展示 |
| MS-6 真实启动 | 模拟器 + `make api` | Today 页从真实后端加载签证建议与每日打扰额度 |
| CRM 回写 | `services/api/tests/test_crm_sync.py` | 文件适配器写待导入 JSON 并标 `PREPARED`；重复触发幂等；未配置适配器显式 `FAILED`；跨租户 404 |
| iOS CRM 同步 | `CrmSyncJob.swift` + `VisitRecordSheet` | 现场记录上传后可发起同步并轮询状态，`PREPARED` 文案不声称外部成功 |
| 显式汇率折算 | `services/api/tests/test_fx_rates.py` | ADMIN 录入/同时间点覆盖；直接与反向汇率；缺汇率 `conversion_complete=false`；跨租户隔离 |
| 预算折算 UI 边界 | `BudgetSummary.swift` | 新增字段向后兼容解码；客户端未传 `base_currency` 时仍展示多币种分列 |
| 行动中心 | `services/api/tests/test_action_center.py` | 建议/报销单/任务/CRM 六类聚合；分类顺序与计数；trip 过滤；跨租户隔离 |
| N2 DST 跨日 | `packages/planning-engine/tests/test_dst.py`、`services/api/tests/test_dst_planning.py` | 春季 23h/秋季 25h 真实瞬时；服务时长按真实经过时间；跨切换日停靠日期与偏移正确 |
| 拜访前简报 | `services/api/tests/test_visit_briefing.py` | 客户/地点/目标/历史/跟进/记忆聚合；目标可编辑；未关联客户与 CRM/资料缺口显式返回；跨租户 404 |
| 通知分级（S8-08） | `services/api/tests/test_notification_priority.py` | P0/P1/P2/P3 确定性规则；入队落库优先级与 issue_key；Feed 分级未读与过滤；非法等级 4xx；跨租户隔离 |
| 当地生活指南（方向五） | `services/api/tests/test_local_guide.py` | 按出行国家返回目录条目；城市贴士只覆盖拜访城市；未知国家与实时源缺口显式返回；跨租户 404 |
| AI 助手对话（页面 05） | `services/api/tests/test_assistant.py`、iOS `AssistantView` | 事实卡片与 llm 回溯；删除只影响对话表；空/超长消息 4xx；跨租户 404；iOS 列表/消息流/卡片展示 |
| iOS 行动中心 | `ActionCenterView` + `ActionCenter.swift` | Today 工具栏入口；分区展示、就地批准/驳回建议、跳转行程 |

## 已知边界

- **AT-001~AT-012 尚无单条串联脚本**：按钮级流程已按模块覆盖（7 条 XCUITest：行动中心、
  新建出行、简报、费用、计划生成/激活、现场记录、Excel 导入、同步），但没有一条脚本从头到尾
  跑完 12 个 AT 场景；会议纪要生成也仍未进 UI 脚本。
- **会议纪要只生成草稿**：不发送、不创建正式任务；发送动作必须由人确认后走业务接口。
- **离线同步的客户合并**只允许填空字段；服务端非空值优先，冲突字段名会在结果 `conflict` 中返回。
- **记忆向量**为本地确定性哈希，用于验证检索链路与延迟；接入真实 embedding 时替换
  `memory_service.embed_text`，表结构不变。
