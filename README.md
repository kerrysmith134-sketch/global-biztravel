# Global Business Travel OS（全球商务差旅智能体系统）

一个**主动式（proactive / agentic）**的全球商务差旅管理系统。它主动感知行程、客户、签证、航班与
当地环境的变化，基于确定性优化算法给出可行行程，并在人审授权下推进执行——把"出差人被迫盯着的
多线事务"变成"系统主动推进、人只做决策"。

设计文档：`docs/design/global-business-travel-os-design-v2.0.md`（v2.0，优化后正式版）
市场情报引擎：`docs/design/market-intelligence-engine-v1.0.md`（通用市场情报领域与 Agent 架构）
H1 实施计划：`docs/review/sprint-h1-execution-plan.md`（领域基础、证据数据架构与工程验收）
H1.0 源码审计：`docs/review/sprint-h1-code-audit.md`（Customer / Evidence / Workflow 复用矩阵、证据缺口与安全发现）
H1.1 领域契约：`docs/architecture/market-intelligence-domain-contract-v1.0.md`（实体、状态机、证据版本化与租户关系约束）
评审报告：`docs/review/design-review-and-optimization-plan.md`
实施进度：`docs/review/implementation-status.md`（按里程碑与批次逐项核对，含验证记录）

## 三个不可动摇的不变量

| 编号 | 不变量 | 代码落点 |
|---|---|---|
| **I1** | 可行性不由 LLM 宣布：LLM 负责理解/研究/协调/解释，**优化算法负责计算，独立验证器负责校验** | `packages/planning-engine/travel_planner/{pipeline,validator}.py`；`app/llm/gateway.py` 只产解释文本 |
| **I2** | 计划不可变、操作可审计、后台任务可恢复、结果可验证 | `plan_versions` 只追加；`plan_attempt_log`/`llm_invocations`/`approvals`；`background_jobs` 租约+心跳；`validator.py` 独立重算 |
| **I3** | 主动系统只建议与准备，不越权执行 | `app/proactive/` 产出 Suggestion（`requires_approval`），执行必须经 `approvals` 记录并由业务用例落地 |

## 快速开始

```bash
bash scripts/bootstrap.sh        # venv + 依赖 + 数据库 + 迁移 + 种子数据（含 Excel 夹具）
make api                         # 起 API（http://localhost:8080/docs）
make worker                      # 起后台 Worker（Outbox / 规划作业 / 主动式扫描 / 复盘 / 指标）
make test                        # 全量后端测试（含真实 PostgreSQL；自动先补种子数据）
make typecheck                   # MyPy 类型检查
make ci                          # 完整门禁（静态/类型/契约/测试/Docker E2E/iOS）
make ios-build                   # 构建 iOS 客户端（模拟器）
make ios-test                    # iOS 单元测试 + UI 冒烟（需 make api + make worker 运行；模拟器名用 IOS_DEST 覆盖）
make e2e                         # 真实后端全链路验收（需 make api 运行）
make stack-up                    # Docker 完整栈（DB + API + Worker）
```

## 本地数据库（已实测）

自建镜像 `gbt-os-postgres:16`：**pgvector/pgvector:pg16（Debian bookworm, linux/arm64）+ PostGIS**。
选择自建而非直接用 `postgis/postgis:16-3.4` 有两个硬理由：

1. 设计文档第 9 章要求情景记忆的向量列与业务表落在**同一个 PostgreSQL 事务边界**内，而 postgis
   官方镜像不含 pgvector；
2. `postgis/postgis:16-3.4` 只有 linux/amd64，在 Apple Silicon 上靠 QEMU 模拟；本镜像原生 arm64。

实测版本：PostgreSQL **16.15**（aarch64）、PostGIS **3.6**、pgvector **0.8.x**。

**角色分层（RLS 的前置条件，第 11.2 章）**

| 角色 | 用途 | 性质 |
|---|---|---|
| `postgres` | 仅用于引导（建角色、建扩展） | 超级用户 |
| `gbt_owner` | Alembic 迁移 | 表 owner（配合 `FORCE ROW LEVEL SECURITY` 仍受策略约束） |
| `gbt_app` | API / Worker 运行时连接 | **非表 owner、非超级用户** → RLS 必然生效 |

连接串见 `.env.example`（本地开发弱口令，非生产密钥）。端口 `55432`。

## 目录结构

```text
global-biztravel/                      # 本工作区
├── apps/ios/                           # SwiftUI 客户端（Xcode 工程，可构建 + 单元测试）
├── services/api/
│   ├── app/                            # api / services / models / llm / workers / data
│   │                                   # （业务规则在 services，路由只做 HTTP 语义）
│   ├── migrations/versions/            # 0001…0048（源码最新迁移：0048_receipt_storage_reconciliation；部署库以 alembic current 为准）
│   └── tests/                          # 单元 + 真实 PostgreSQL 集成 + 验收场景
├── packages/planning-engine/           # 纯计算规划引擎（无 IO、不碰数据库、不订票）
├── contracts/openapi.yaml              # 契约唯一真相源（由 make openapi 导出）
├── docs/{design,review,architecture,decisions,runbooks,acceptance-tests}
├── infra/docker/                       # 自建 PostgreSQL 镜像 + compose + initdb 引导
├── scripts/                            # bootstrap / 契约导出 / 开发种子数据
├── requirements.txt / requirements-dev.txt
├── pyproject.toml                      # 仅工具配置（ruff / pytest）
└── Makefile
```

**关键约束**：业务逻辑不写在 API 路由里。例如激活计划是
`HTTP Endpoint → ActivatePlanUseCase → Authorization → Domain → Transaction → Outbox → Audit`。

## 规划引擎流水线（第 6 章）

```text
Input Normalizer → Feasibility Checker → Candidate Generator
→ Optimization Solver → Independent Validator → Explanation Generator
```

- 求解器**确定性**：相同 `PlanningProblem` ⇒ 相同 `PlanningResult`（固定种子、单线程、禁用并行）。
- **OR-Tools 优先 + 自研确定性启发式兜底**（最近邻 + 2-opt + 时间窗修复）。
- 独立验证器**不采信**求解器自报的耗时/里程，从坐标重新计算并逐条裁决约束。
- 超预算 ⇒ 返回启发式可行解且 `degraded=true`；**绝不返回不可行却说可行**。

规划引擎不导入 `sqlalchemy`/`httpx`，这一点由测试强制。

## 主动式运行时（第 8 章）

```text
触发源四分类 SCHEDULED / EVENT / STATE / EXTERNAL
  → ProactiveTrigger → 评估 → ProactiveSuggestion（含理由 + 证据 + 是否需人审）
  → 抑制与节流（去重键 / 冷却窗口 / 每日打扰上限 / 置信度阈值）
  → {SUPPRESSED | APPROVED → EXECUTED} → AUDITED → LEARNED
```

主动系统**不能**：对外发送、预订、提交签证、绕过授权改正式业务数据。

## 验收

- `AT-001~AT-012`：MVP-1 业务闭环（创建多国 Trip → Excel 导入 → 校验 → 生成候选行程 → 地图/顺序/时间/费用/约束验证 → 增删调整 → 新版本 + diff → 激活 → 现场记录（离线可存）→ 网络恢复同步 → 会议纪要草稿）。
  iOS UI 自动化按真实后端流程拆分：`testAcceptanceJourneyChain` 覆盖 AT-001~006 与 AT-010/012；
  `testPlanChangeAndDiffFlow` 覆盖 AT-007/008；同步中心路径由独立 UI 冒烟覆盖，逐项同步结果由后端 E2E 与
  `test_records_sync_integration` 验证。场景映射见 `docs/acceptance-tests/ios-ui-automation.md` 与
  `docs/acceptance-tests/backend-extended.md`。
- `AT-P01~AT-P04`：主动式（签证时效触发 / 重复触发抑制 / 人审→受控执行→审计 / 驳回→误报回收）。
  场景到测试用例的映射见 `docs/acceptance-tests/proactive-runtime.md`。
- `AT-S01`：组织 A 会话访问组织 B 数据必须全部失败（数据库级 RLS）。

验收必须在**真实客户端、后端与数据库**之间完成，不得以静态页面、模拟 API 返回值或预置演示数据替代。
当前自动化验收基线：后端最新 CI（2026-11-25）为 **473 passed / 1 skipped**（真实 PostgreSQL）；iOS 152 个 XCTest 在同一轮 CI **全部通过**（0 failures）；真实后端 UI 自动化目标含 10 条用例，当前 CI 已成功编译测试 target（`build-for-testing`），但该轮未执行 UI 运行时。最近一次完整真实后端 UI 运行记录为 2026-11-09：9 条 UI 用例通过。`make e2e` 真实后端全链路、`make contract-check` 契约漂移检查持续作为后端验收门禁。