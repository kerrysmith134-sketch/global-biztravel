# 工程实施进度核对（对设计文档 v2.0 / 评审计划 v1.0）

- 核对时间：2026-10-28
- 核对方式：逐条对照设计文档第 17.2 章里程碑与评审报告第 4 章批次；在真实 PostgreSQL 上执行
  后端全量测试、迁移漂移检查、契约漂移检查、Worker 单轮运行、真实后端端到端脚本、规划基准、
  iOS 构建与单元测试，以及模拟器 UI 冒烟测试。
- 结论：**MS-1 ~ MS-7 全部完成；批次 1、批次 2 全部完成；批次 3 的 N1/N3 已完成，N2 仅剩 DST 专项测试。** 所有"主动式"分类（SCHEDULED / EVENT / STATE / EXTERNAL）均有落点。

## 1. 里程碑核对

| 里程碑 | 要求 | 状态 | 证据 |
|---|---|---|---|
| MS-1 | 骨架 + PostgreSQL/PostGIS + 迁移 + RLS | ✅ | 迁移 `0001`~`0013`；`0010_security_rls` 全库兜底；`test_all_org_tables_force_rls` |
| MS-2 | 身份/组织/Trip/客户/地点 + Excel 两阶段导入 | ✅ | 导入 API、真实校验报告、E2E 脚本第 3 步 |
| MS-3 | 规划引擎 + 独立验证器 + Outbox/Worker | ✅ | 六段流水线、双轨求解器、**严格覆盖不变量**（ADR-0007）、租约/DLQ |
| MS-4 | 计划版本/差异/激活 + 并发冲突 | ✅ | 不可变守卫、`If-Match`、单个 ACTIVE、diff |
| MS-5 | 现场记录 + 离线同步 + 主动式 + 记忆 | ✅ | `visit_records`、`sync:push` 逐项结果、主动式四类触发、三层记忆与复盘 |
| MS-6 | iOS 客户端 | ✅ | App 入口 + Today/Trips/计划/现场记录/同步/记忆/设置；构建通过；7 单测 + 1 UI 冒烟 |
| MS-7 | 交付校验 | ✅ | `scripts/acceptance_e2e.py` 真实后端全链路（8 步）通过；iOS UI 冒烟连接真实后端通过 |

> MS-7 的边界：UI 自动化覆盖"启动 → Today → 出行列表 → 详情 → 同步页"；AT-001~AT-012 的
> 业务全链路由 `acceptance_e2e.py` 在真实 HTTP + PostgreSQL + Worker 上完成。没有把每个业务
> 按钮都用 XCUITest 点一遍（Excel 文件选择器在模拟器里的自动化成本高于收益）。

## 2. 批次 1（MVP-1.1）核对

| 编号 | 事项 | 状态 | 落点 |
|---|---|---|---|
| M5 | 数据库级租户隔离 | ✅ | ENABLE + FORCE + `tenant_isolation`；应用角色非 owner；事务级 GUC |
| M6 | 指标与验收 | ✅ | 9 项指标、快照幂等、Worker 周期写入、SLO 目标 |
| M1 | 主动式运行时 | ✅ | SCHEDULED / EVENT / STATE / **EXTERNAL** 四类触发；抑制三层；审批+审计；通知通道 |
| M4 | 可靠性 | ✅ | 幂等、Outbox、任务租约/心跳/退避/DLQ |
| M3 | LLM 编排与工具契约 | ✅ | Provider 抽象、调用审计、缓存、schema 校验/重试、模板降级、工具目录 |
| M2 | Agent 记忆与复盘 | ✅ | 三层记忆、pgvector、混合检索、复盘 Worker、合规删除 |

## 3. 批次 2 / 3 核对

| 编号 | 事项 | 状态 | 落点 |
|---|---|---|---|
| S3 | 可观测性 | ✅ | JSON 请求日志（trace/org/trip/usecase/latency）、按 Trip 的审计时间线 `/v1/trips/{id}/audit` |
| S5 | 版本与演进治理 | ✅ | ADR-0002~0009、迁移不可变、`/v1` 前缀、`make contract-check` |
| S1 | 外部集成边界 | ✅ | `external_signals` + `POST /v1/integrations/signals`；主动式 EXTERNAL 规则（ADR-0005） |
| S4 | PII 与合规 | ✅ | 数据字典；记忆合规删除；字段级加密（`GBT_FIELD_ENCRYPTION_KEY`）；留存脱敏 Worker 与 `POST /v1/admin:retention`；KMS 轮换待接 |
| S6 | 契约同步 | ✅ | `contracts/openapi.*` + `make contract-check`（路径/schema/内容三层比较） |
| S2 | 性能/容量/成本 | ✅ | `docs/architecture/performance.md`（容量假设/时间预算/LLM 成本/扩容顺序）+ `make bench` |
| N3 | 求解器基准/回归 | ✅ | 同一基准 20 轮内 OR-Tools/启发式均覆盖全部停靠、problem_hash 稳定 |
| N2 | 时区/货币 | 🟡 | 规划按 `timezone_home` 切日、iOS 侧 ISO8601 工具；DST 专项测试未补齐 |
| N1 | 审批矩阵抽象 | ✅ | `approval_policies`（按风险等级要求最低角色）+ 批准时强制执行；多级会签仍待扩展 |

## 4. 本轮新增能力

- **EXTERNAL 触发源**：外部信号幂等接入、按国家/出行匹配、4 类建议、人审后只准备摘要。
- **通知/推送**：建议 → Outbox → 通知入队 → Worker 投递 → 已读；未配置 APNs 时显式失败。
- **可观测性**：每个请求一条 JSON 日志（含延迟与 trace）；审计时间线聚合建议/审批/Outbox/记录/规划尝试。
- **严格覆盖不变量**：修复"0 停靠仍 feasible"的静默错误；两层求解器都覆盖失败 ⇒ `SolverFailed`。
- **交付脚本**：真实后端 E2E、契约漂移检查、规划基准、iOS UI 冒烟目标。
- **合规**：记忆遗忘接口、字段加密、留存脱敏与 PII 数据字典。
- **事务时序修复**：写接口的 2xx 可能在提交前返回（E2E 暴露）；改为响应开始前提交/回滚（ADR-0010）。

## 5. 验证记录

| 命令 | 结果 |
|---|---|
| `make test` | **176 passed**（真实 PostgreSQL） |
| `make lint` / `make check` | 通过；迁移无漂移 |
| `make openapi` / `make contract-check` | 42 路径 / 75 schema；契约一致 |
| `make e2e`（API + PG + Worker） | 创建 Trip → Excel 导入 → 求解 → 激活 → 记录 → 同步 → 纪要 → 复盘 → 指标 → 审计，**全部通过** |
| `make bench` | heuristic p50 0.4ms / p95 0.5ms；ortools p50 0.3ms / p95 0.4ms；6/6 停靠覆盖，hash 稳定 |
| `make ios-build` | BUILD SUCCEEDED |
| `make ios-test` | **7 单测 + 1 UI 冒烟通过**（UI 连接真实后端） |
| 模拟器启动 | Today 页展示真实签证建议（置信度 85%、当日 1/3 次打扰） |

## 6. 迁移清单（当前 head `0013`）

```text
0001_core_identity         0007_agent_memory
0002_trip_domain           0008_llm_invocations
0003_reliability_core      0009_metric_snapshots
0004_planning_reliability  0010_security_rls
0005_proactive_runtime     0011_external_signals
0006_visit_records         0012_notifications
                           0013_approval_policies
```

## 7. 仍缺项（不影响 MVP-1 能力，生产前需补）

1. **真实外部供应商**：签证/汇率/天气/航班的数据源、鉴权与字段映射未接入；当前只提供标准信号接入。
2. **真实 APNs/邮件通道**：需要证书、设备 token 注册与退订；当前 `inapp` 可用，`apns` 显式失败。
3. **KMS 密钥托管与轮换**：字段加密 key 目前来自环境变量，轮换需回填脚本。
4. **多级会签**：审批策略已支持"按风险等级要求最低角色"，多人会签/审批链仍待扩展。
5. **真实 embedding 凭证**：provider 已实现（hash / openai_compatible），生产需配置 base_url/key。
6. **事务时序**：已修成「响应开始前提交」（ADR-0010）；流式端点若将来引入需重新设计。
6. **N2 时区/货币**：规划与客户端已有基础处理，DST 专项测试未补齐。

> 迁移编号以本仓库实际顺序为准（设计文档的 `0008`~`0013` 是未拆批时的规划编号）。
> 本文件随每个里程碑完成后更新，不替代设计文档。
