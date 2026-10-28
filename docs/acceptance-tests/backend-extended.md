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
| MS-7 客户端 UI 冒烟 | `GlobalTravelOSUITests` | 启动 → Today → 出行列表 → 详情 → 同步页，连接真实后端 |
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
| MS-6 真实启动 | 模拟器 + `make api` | Today 页从真实后端加载签证建议与每日打扰额度 |

## 已知边界

- **AT-001~AT-012 的完整 UI 脚本尚未编写**：各环节分别有后端验收与客户端单测，但没有一条
  XCUITest 串起"创建→导入→规划→激活→记录→同步→纪要"。这是 MS-7 剩余项。
- **会议纪要只生成草稿**：不发送、不创建正式任务；发送动作必须由人确认后走业务接口。
- **离线同步的客户合并**只允许填空字段；服务端非空值优先，冲突字段名会在结果 `conflict` 中返回。
- **记忆向量**为本地确定性哈希，用于验证检索链路与延迟；接入真实 embedding 时替换
  `memory_service.embed_text`，表结构不变。
