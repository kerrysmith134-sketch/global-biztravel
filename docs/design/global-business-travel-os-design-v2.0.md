# 全球商务差旅智能体系统 · 优化后设计文档 v2.0

> **文档性质**：本文档是《ChatGPT_全球商务出差规划_20261028-1244.md》（38,017 行、2026-10-27~28 多轮对话记录）的**优化后正式版本**。它不另起新稿：文档第 2 节给出原文区段到本文章节的**追溯矩阵**，原文中已固化的四份 v1.0 基线（《完整应用架构 v1.0》《规划引擎正式契约 v1.0》《数据库 DDL 与迁移设计 v1.0》《MVP-1 工程执行基线 v1.0》）作为**保留基线**被完整承接；本文只在关键缺口处【新增】【修订】【强化】。
> **标注约定**：`【保留】`原文内容原样承接；`【强化】`原文方向正确、本次明确为强制规范；`【修订】`对原文口径作实质性修正；`【新增】`原文缺失、本次补齐。
> **版本**：v2.0 ｜ 状态：设计已优化，待工程冻结 ｜ 日期：2026-10-28

---

## 1. 系统一句话定义

**一个主动式（proactive / agentic）的全球商务差旅智能体系统：它主动感知行程、客户、签证、航班与当地环境的变化，基于确定性优化算法给出可行行程，并在人审授权下推进执行，把"出差人被迫盯着的多线事务"变成"系统主动推进、人只做决策"。**

三个不可动摇的不变量（贯穿全部设计）：

- **I1 可行性不由 LLM 宣布**：LLM 负责理解、研究、协调与解释；优化算法负责计算；**独立验证器**负责校验结论。任何行程可行性结论必须来自确定性计算 + 独立校验。
- **I2 计划不可变、操作可审计、后台任务可恢复、结果可验证。**
- **I3 主动系统只建议与准备，不越权执行**：对外发送、预订、提交签证等外部动作**必须人审后由业务服务执行**；Agent 不得直连数据库、不得绕过授权。

---

## 2. 追溯矩阵（原文区段 → v2 章节）

| 原文区段（行号） | 原文内容 | 本文对应章节 | 处理 |
|---|---|---|---|
| 1–400 | 产品定位、6 个核心方向 | 3、4 | 【强化】"主动式"补机制定义 |
| 400–803 | 真实需求重解、五个核心问题 | 3 | 【保留】 |
| 803–1192 | 需求基线、AI 出差决策引擎 | 4、8 | 【保留】+【强化】接主动运行时 |
| 1193–5270 | 正式定位、核心工作流、多国行程规划引擎、应用架构 | 5、6 | 【保留】 |
| 5270–6221 | 完整应用架构 v1.0、Schema、Agent 工作流、工程目录 | 5、7、12 | 【保留】+【修订】Agent 工作流补运行时 |
| 6221–7310 | 业务闭环与边界、API 契约、可靠性规范、证据系统、测试、验收 | 7、10、13、14、16 | 【修订】可靠性/验收细化 |
| 7311–7850 | 数据库 Schema 与迁移规范 v1.0 | 12 | 【保留】+【新增】RLS |
| 7851–28183 | 接口五项强制规则、API 约定、核心 Schema、Excel 导入、路线优化、计划修改确认 | 13 | 【保留】 |
| 28184–28744 | MVP-1 工程实施规范 | 17 | 【保留】 |
| 28745–29261 | 工程结构与领域模型（仓库结构） | 5.4 | 【保留】 |
| 31849–32632 | 数据库 DDL 与迁移设计 v1.0 | 12 | 【保留】+【新增】新表 |
| 32633–33240 | OpenAPI | 13 | 【保留】 |
| 33241–34236 | 规划引擎正式契约 v1.0 | 6 | 【保留并冻结】 |
| 34237–35582 | 交通供应商层、联调 | 15 | 【保留】+【新增】能力矩阵 |
| 35591–37292 | iOS 架构与工程 | 5.4、13 | 【保留】 |
| 37314–38017 | MVP-1 工程执行基线 v1.0 | 17 | 【保留】 |

---

## 3. 产品定位与价值主张【强化】

### 3.1 六个核心方向（【保留】，原文 1–400）

1. AI 全球出差规划师 2. 签证与跨境出行管家 3. 商务客户拜访智能体 4. 实时行程协调员 5. 当地生活与文化助手 6. 差旅费用与财务闭环

### 3.2 "主动式"的机制定义【新增，本次最重要修订】

原文把系统称为"主动式 agentic"，但正文固化的机制是响应式的。为避免定位与实现脱节，本节明确："主动式"**不是**"有个定时任务"，而是同时满足以下四条的运行时：

1. **持续感知**：系统在不依赖用户发起请求的前提下，持续消费内部事件（计划激活、停靠变更、拜访记录回填）与外部变化（航班状态、签证政策、汇率、天气中断）。
2. **目标保持**：跨天、跨会话保持对"本次出行目标与约束"的理解，并记住"哪些方案已试过、哪些已被否掉、为什么被否"（见第 9 章记忆层）。
3. **主动提案**：基于感知与目标，主动生成**建议（Suggestion）**，附理由与证据，并标注是否需人审。
4. **受控执行 + 复盘**：人审通过后由业务服务执行，全过程审计，行程结束后复盘写回记忆。

### 3.3 主动式边界（与原文 37314–38017 的 7 项"暂不承诺"一致）【强化】

主动系统**只做到"建议 + 准备"**，以下必须在人审后由业务服务执行，且系统不承诺自动化：自动提交签证、自动购票订房、自动发送客户邮件、全自动 CRM 双向同步、实时交通事故预测、完全自主跨国行程变更、复杂企业差旅审批矩阵。

---

## 4. 需求基线与领域模型【保留】

### 4.1 五个核心问题（原文 400–803）

真实出差人面对的问题：①信息碎片化（航班/酒店/客户/签证分散）；②时间窗与地理冲突难解；③变更成本高（一处改期牵动全局）；④跨境合规与签证时效；⑤费用与合规闭环缺失。

### 4.2 AI 出差决策引擎（原文 803–1192）【保留 + 强化】

保留其"感知—评估—决策—解释"的职责划分。本次**强化**：引擎的所有输出必须携带 `evidence[]`，且"可行性"结论只能由第 6 章验证器给出；引擎自身不得直接修改正式业务数据（见 5.2 数据所有权）。

---

## 5. 总体架构【保留 + 修订】

### 5.1 架构基线（原文 37314–38017）

| 层 | 技术 |
|---|---|
| 客户端 | iOS / SwiftUI |
| 后端 | Python + FastAPI（业务层，唯一真相源） |
| 数据库 | PostgreSQL 16 + PostGIS 3.4 |
| 规划 | 独立领域模块（约束优化算法） |
| 部署 | 模块化单体 + 持久化后台任务 |
| 契约 | OpenAPI 3.1 |
| 隔离 | 组织级隔离（**数据库层强制**，见第 11 章） |

核心原则【强化】：**计划不可变、操作可审计、后台任务可恢复、结果可验证。**

### 5.2 架构原则与数据所有权【保留 + 强化】

- 模块化单体，不提前拆微服务。
- 规划引擎（`packages/planning-engine`）**不直接与用户交互、不直接操作数据库、不执行外部预订**。
- **数据所有权**（强化为强制）：规划引擎可"计算"计划，Agent 可"提出"行动（Proposal），**只有业务服务才能按授权规则修改正式业务数据**。

### 5.3 分层调用链【修订，补运行时】

```text
iOS App
  └─ FastAPI Business Layer（唯一真相源，授权 + 事务 + 审计）
       ├─ Domain Services（Trip / Customer / Place / Plan / VisitRecord）
       ├─ Application Use Cases（用例编排，业务逻辑不写在路由里）
       ├─ Proactive Runtime（触发 → 建议 → 人审 → 执行 → 审计，第 8 章）
       ├─ Memory Service（事实/过程/情景记忆，第 9 章）
       ├─ Planning Orchestrator（第 6 章）
       │     └─ packages/planning-engine（纯计算，无 IO）
       ├─ LLM Gateway（第 7 章）
       ├─ Outbox + Workers（第 10 章）
       └─ Integrations（供应商/签证/汇率/天气，第 15 章）
```

### 5.4 目标仓库结构【保留】（原文 28745–29261）

```text
global-travel-os/                      # 本工作区：/Users/kerry/Coding Work/个人作品/zhi-tu
├── apps/ios/{App,Core,Domain,Data,Features/{Authentication,Today,Trips,Map,Planning,VisitWorkspace,OfflineSync},Tests}
├── services/api/{app/{api,domain,application,infrastructure,workers,integrations,security},migrations,tests}
├── contracts/openapi.yaml
├── packages/planning-engine/{problem.py,solver.py,validator.py,tests}
├── docs/{design,review,architecture,decisions,runbooks,acceptance-tests}
├── infra/{docker,deployment}
├── scripts/, pyproject.toml, Makefile, README.md
```

关键约束【强化】：**业务逻辑不写在 API 路由中**。例：激活计划为 `HTTP Endpoint → ActivatePlanUseCase → Authorization → Domain → Transaction → Outbox → Audit`。

---

## 6. 规划引擎正式契约 v1.0【保留并冻结】

### 6.1 架构原则（不变量 I1）

> LLM 负责理解、研究、协调和解释；优化算法负责计算；独立验证器负责检查结果。**任何一个 LLM 都不能仅凭自己的判断宣布行程可行。**

### 6.2 流水线（原文 33241–34236）

```text
iOS App → FastAPI Business Layer → Planning Orchestrator
   Input Normalizer → Feasibility Checker → Candidate Generator
   → Optimization Solver → Independent Validator → Explanation Generator
→ PlanningResult → PlanVersion + Validation Report → User Review → Activation
```

### 6.3 输入/输出契约

`PlanningProblem`（输入，规范化后）：

```json
{
  "trip_id": "uuid",
  "organization_id": "uuid",
  "timezone_home": "Asia/Shanghai",
  "origin": {"place_id": "uuid", "lat": 0.0, "lon": 0.0},
  "window": {"start": "2026-11-03T00:00:00+08:00", "end": "2026-11-07T23:59:59+08:00"},
  "stops": [{
    "visit_id": "uuid", "place_id": "uuid", "lat": 0.0, "lon": 0.0,
    "service_minutes": 90,
    "time_windows": [{"start": "2026-11-04T09:00:00+01:00", "end": "2026-11-04T12:00:00+01:00"}],
    "priority": 1, "business_value": 50000, "movement_mode": "GROUND"
  }],
  "objective": {"primary": "MINIMIZE_TRAVEL_AND_MAXIMIZE_VALUE", "weights": {"travel": 1.0, "value": 0.001}},
  "constraints": {"max_stops_per_day": 4, "immovable_stop_ids": [], "locked_order": []},
  "solver_budget_seconds": 10
}
```

`PlanningResult`（输出）：

```json
{
  "trip_id": "uuid", "problem_hash": "sha256:...", "solver": "ortools|heuristic",
  "days": [{"date": "2026-11-04", "stops": [{"visit_id": "uuid", "sequence": 1,
             "arrive": "...", "depart": "...", "travel_minutes": 42}]}],
  "objective_value": 1234.5,
  "validation": {"feasible": true, "checks": [{"rule": "TIME_WINDOW", "status": "PASS"}]},
  "unplaced": [{"visit_id": "uuid", "reason": "NO_FEASIBLE_SLOT"}],
  "degraded": false, "explanation_ref": "llm_invocation_id"
}
```

#### 6.3.1 时间语义：多天连续路径【强化，本次修订】

这是本契约中**唯一**必须逐字冻结的时间语义，求解器、物化器与独立验证器三者共用：

1. **整条行程是一条连续路径** `origin → s1 → s2 → … → sn`。第 1 天自 `problem.origin`
   出发；第 k 天自**第 k-1 天最后一个已排停靠**的位置出发。
2. 若第 k-1 天没有停靠，则继续向前回溯到最近一个"有停靠的日子"；仍无则回到 `origin`。
3. **跨天不重置位置、不模拟返程**；`travel_minutes_total` 即这条路径上各段行程时间之和。
4. 每天的**出发时刻**取该日区间起点（`DayWindow.open`）；同日续排则接续上一个停靠的 `depart`。
5. **日归属由物化器决定，不由求解器决定**（见 §6.5）。

> ⚠️ 反例（**已修正的历史根因**）：早期实现让**每一天都从 `origin` 重新起算**，
> 等价于把跨洲段（上海 → 柏林，最省时的航空也要约 840 分钟）重复计入每一天。
> 于是第 2 天之后的所有停靠都被算成"当天到不了"，多国行程必然不可行——
> 那是**建模错误**，不是现实约束。同一缺陷也存在于 OR-Tools 模型（早期每天一个车场）。
> 该缺陷由"2026 Q4 欧洲多国商务出行"在真实端到端演练中被暴露：4 个拜访全部落到
> `unplaced`，Trip 被独立验证器判为不可行。

配置层同一口径：`PlanningProblem.window` 的 `start` 应是**首日的本地 00:00**
（首日就要完成跨洲航段并落在当日会议窗内），而非当日的某个上班时刻。

### 6.4 确定性要求【强化，配合第 7 章】

- 求解器必须**确定性**：相同 `PlanningProblem` 产生相同 `PlanningResult`（固定随机种子、禁用非确定性并行）。
- `problem_hash` 用于缓存与幂等；同一 hash 重复请求命中缓存，不重复求解。
- 求解超预算 ⇒ 返回启发式可行解并在结果标记 `degraded=true`，**绝不返回不可行却说可行**。
- **职责切分（与 §6.3.1 配套）**：优化求解器只输出**访问顺序**；分钟级到达/离开与
  **日归属**一律由物化器（`scheduling.build_schedule`）统一计算，避免求解器与验证器
  各算一套时间。
- **覆盖不变量**：成功解必须覆盖**全部** `eligible` 停靠；判定对象是**物化之后**的停靠集合，
  而非求解器返回的顺序。物化后仍有人没排进去 ⇒ 视同求解失败 ⇒ 沿兜底链降级并标
  `degraded=true`。"排 0 个停靠"永远不可能作为成功解交给验证器。

### 6.5 求解器选型【修订】

- 主选 **OR-Tools**（支持 TSP/VRP/VRPTW/CVRPTW，含 Python 绑定）。

    > 模型形态采用**单车松弛**（与 §6.3.1 的连续路径语义配套）：只放**一辆车**，
    > 节点 0 = 起点车场（`origin`）、节点 1 = 终点车场、节点 `i+2` = 第 i 个停靠；
    > 时间维覆盖**整个窗口**，各停靠取其时间窗相对窗口起点的**凸包**并 clamp。
    > 时间维单调递增 ⇒ 窗口晚的停靠天然被排到后面，**顺序由窗口自身约束出来**，
    > 日归属则完全交给物化器。
    >
    > ⚠️ 反例（**已修正的历史根因**）：早期给**每一天**各放一辆车（每天一个
    > start/end 车场），等价于"每天从 `origin` 重新起算"，跨洲段被逐日重复计入；
    > 叠加"取起点交通模式"（`origin` 的 GROUND ⇒ 上海 → 柏林 = 8418 分钟）与
    > 可丢弃停靠（`AddDisjunction` + 惩罚远小于真实行程成本），模型会输出"什么都不排"
    > 的退化解，而验证器的 `STOP_COVERAGE` 只校验"已排 ∪ 未排 = 全集"，仍判可行——
    > 一条**静默错误**。修正后所有 `eligible` 均为强制访问，无解即 `SolverFailed`。
    >
    > 行程时间口径**必须取目的停靠的交通模式**，与物化器逐段同源；进入终点车场的
    > 返程弧计 0（整条路径不模拟返程）。
- **兜底自研确定性启发式**（最近邻构造 + 2-opt + 时间窗可行性修复），保证在 OR-Tools 不可用时仍可运行、可测试、可重放。
- VROOM（pyvroom）在 Apple Silicon 上编译受限、jsprit 为 Java 方案，**不选为 MVP 主路径**。

---

## 7. LLM 编排与工具契约 v1.0【新增（M3）】

### 7.1 Provider 抽象

统一网关 `LLMGateway`，屏蔽后端差异并支持 failover：

```python
class LLMProvider(Protocol):
    name: str
    def complete(self, req: LLMRequest) -> LLMResponse: ...

@dataclass
class LLMRequest:
    task: str                 # e.g. "explain_plan" / "draft_email" / "summarize_trip"
    prompt_template_id: str   # 版本化模板
    prompt_version: str
    variables: dict
    schema: dict | None       # 结构化输出 JSON Schema，强校验
    seed: int | None
    max_tokens: int
    budget: "TokenBudget"
```

凭证一律来自环境变量/密钥管理【红线 RL-05】，禁止硬编码，禁止写入任何文件或记忆。

### 7.2 调用记录与审计

表 `llm_invocations(id, organization_id, task, model, prompt_template_id, prompt_version, input_hash, output_json, tokens_in, tokens_out, latency_ms, cost_estimate, degraded, trace_id, created_at)`。
每条 Agent 产出（解释、建议理由、草拟文本）都必须能回溯到一条 `llm_invocation`。

### 7.3 Tool 契约【强化 I3】

工具声明式定义：

```json
{"name": "trip.get_plan", "input_schema": {...}, "scope": "trip:read",
 "requires_approval": false, "idempotent": true, "handler": "usecase:GetPlan"}
```

规则：①工具一律通过业务用例调用，**禁止 Agent 直连数据库**；②`requires_approval=true` 的工具（对外发送、预订等）必须经人审；③写类工具必须提供 `Idempotency-Key`。

### 7.4 确定性与重放

- 所有"计算"步骤不依赖 LLM；LLM 仅用于理解/解释/草拟，输出必须过 `schema` 校验，校验失败重试一次后降级。
- LLM 响应缓存键 = `(task, prompt_template_id, prompt_version, model, input_hash, seed)`，支持重放。
- `temperature` 默认 0；`seed` 固定。

### 7.5 降级路径

LLM 不可用或超预算 ⇒ 规划仍由确定性求解器完成，`Explanation Generator` 降级为模板化说明，`PlanningResult.degraded=true` 且 `llm_invocations` 记录 `degraded=true`。

---

## 8. 主动式运行时规范 v1.0【新增（M1，本次核心）】

### 8.1 触发源四分类

| 类型 | 触发条件示例 | 最小触发间隔 | 数据来源 |
|---|---|---|---|
| `SCHEDULED` | 出发 T-72h 签证/证件核验；T-24h 行程确认；行程结束后 24h 复盘 | 每 (trip, rule) 1 次 | 调度器 |
| `EVENT` | 客户改期、航班状态变化、计划版本被激活、拜访记录回填 | 即时 | 内部事件（Outbox） |
| `STATE` | 客户地址坐标缺失、时间窗冲突未解、停靠超日上限 | 12h | 领域状态扫描 |
| `EXTERNAL` | 签证政策变更、汇率波动、天气中断 | 24h | 集成层（第 15 章） |

触发条件以声明式规则表达，例：`SCHEDULED(visa_check): trip.departure - 72h AND trip.has_cross_border_stop`。

### 8.2 触发 → 建议契约

```json
// ProactiveTrigger
{"id":"uuid","organization_id":"uuid","trip_id":"uuid","type":"SCHEDULED",
 "rule_id":"visa_check","payload":{...},"occurred_at":"...","dedupe_key":"visa_check:2026-11-03"}
// ProactiveSuggestion
{"id":"uuid","trip_id":"uuid","source_trigger_id":"uuid",
 "suggestion_type":"VISA_REQUIREMENT_REMINDER",
 "rationale":"目的地要求商务签证，办理需 10 个工作日，距今仅 7 个工作日",
 "evidence":[{"kind":"VISA_REQUIREMENT","ref":"...","retrieved_at":"..."}],
 "proposed_action":{"kind":"PREPARE_CHECKLIST","tool":"visa.prepare_checklist","params":{...}},
 "confidence":0.92,"requires_approval":true,"severity":"HIGH"}
```

### 8.3 建议生命周期状态机

```text
DETECTED → EVALUATED → PROPOSED → {SUPPRESSED | APPROVED → EXECUTED} → AUDITED → LEARNED
```

每步落 `proactive_suggestions` 表并写审计；`EXECUTED` 仅当 `requires_approval=false` 或已存在 `APPROVED` 记录。

### 8.4 抑制与节流

- 去重键 `(organization_id, trip_id, suggestion_type, dedupe_key)` 唯一，重复触发不再产生新建议。
- **冷却窗口**：同类型建议提出后 N 小时内不再重复（默认 24h）。
- **每日打扰上限**：每出差人每日推送 ≤ `daily_cap`（默认 3），超限仅入库不推送，按严重度排序择优推送。
- **低置信度抑制**：`confidence < threshold`（默认 0.6）只入库，不推送。
- **误报回收**：用户忽略/驳回的建议标记 `dismissed`，进入记忆层（第 9 章）降低同类权重（可测指标：误报抑制率）。

### 8.5 权限边界【强化 I3】

- 主动系统**能**：生成建议、准备材料（草拟清单/邮件草稿/变更方案）、标记冲突、发起人审。
- 主动系统**不能**：对外发送、预订、提交签证、修改正式业务数据（除经授权用例）。
- 所有 `requires_approval=true` 的建议，执行前必须有一条 `approval` 记录（人、时间、决策）。

### 8.6 主动动作的可靠性

主动动作（尤其是对外动作）必须：①通过 Outbox 投递，幂等；②在 Worker 中执行且可恢复（第 10 章）；③失败进 DLQ 并产生告警建议。

---

## 9. Agent 记忆与复盘规范 v1.0【新增（M2）】

### 9.1 三层记忆

| 层 | 内容 | 存储 | 特征 |
|---|---|---|---|
| 事实记忆 Semantic | 出差人偏好（航空/酒店/作息/饮食）、客户档案与拜访历史、行程结果 | PostgreSQL 关系表 + Evidence | 强一致、结构化 |
| 过程记忆 Procedural | 尝试与否决记录：生成过哪些候选、验证器否决了什么、否决原因 | `plan_attempt_log` | 变更频繁 |
| 情景记忆 Episodic | 以行程/客户为维度的滚动摘要（摘要+决策+待办+教训） | `trip_memory_summaries`（含向量列） | 持续追加 |

**关键决策【修订】**：三层记忆**全部落在同一 PostgreSQL 事务边界内**（情景记忆用 `pgvector` 扩展存向量），不引入独立向量库/图库，避免"跨系统写非事务、脏记忆、运维成本"问题。

### 9.2 混合检索

`retrieve(query, scope)`：①关键词检索（`tsvector` / BM25 近似）；②语义检索（pgvector 余弦相似度）；③RRF 融合；④时间衰减（越近权重越高）；⑤实体/组织/出差人过滤。检索 P95 目标 < 100ms（租户内）。

### 9.3 失效与版本（主动遗忘）

记忆条目字段：`valid_from, valid_until, confidence, superseded_by, source_ref`。
- 事实变更 ⇒ 旧条目 `valid_until=now`，新条目指向 `superseded_by`。
- 冲突条目并存时，按 `confidence` 与时间优先。
- 提供 `forget(entity_ref)` 支持合规删除（配合第 11 章 PII）。

### 9.4 复盘闭环

行程 `COMPLETED` 后由 Worker 触发：①汇总实际执行 vs 计划差异；②提炼决策与教训；③写回情景记忆并更新事实记忆（偏好、客户画像）；④生成下一次出行的改进建议。复盘产物可被规划引擎与主动运行时读取。

### 9.5 与 I1 的关系

记忆只提供"上下文与偏好"，**不得**作为可行性依据；可行性仍只由求解器 + 验证器决定。

---

## 10. 可靠性规范 v1.0【新增（M4）】

### 10.1 幂等

- 所有写命令接受 `Idempotency-Key` 头；表 `idempotency_records(key, organization_id, request_hash, response_json, status, created_at)`。
- 相同 key：若 `request_hash` 不同 ⇒ `409 IDEMPOTENCY_KEY_REUSED`；相同 ⇒ 返回首次结果。
- 每个命令在文档中明确幂等语义（见第 13 章 API 表）。

### 10.2 Transactional Outbox

- 业务事务内写 `outbox_events(id, organization_id, aggregate_type, aggregate_id, event_type, payload, status, attempts, next_attempt_at, created_at, processed_at)`。
- 独立 `OutboxDispatcher` 拉取 `PENDING` 事件投递给对应消费者；消费者幂等（按 `event_id` 去重）。
- `status ∈ {PENDING, PROCESSING, DONE, DEAD}`；超过重试上限 ⇒ `DEAD` + 告警。

### 10.3 后台任务状态机

表 `background_jobs(id, organization_id, job_type, payload, status, attempts, max_attempts, lease_owner, lease_expires_at, last_error, created_at, updated_at)`。
`PENDING → RUNNING → SUCCEEDED | FAILED | DEAD`；Worker 以**租约 + 心跳**认领任务，崩溃后租约过期可被重新认领（对应"任务可恢复"）。

### 10.4 重试与死信

指数退避（基数 2s，上限 5 分钟，抖动）；超 `max_attempts` ⇒ `DEAD` 且生成主动告警建议。

### 10.5 离线同步冲突矩阵

| 实体 | 冲突策略 | 说明 |
|---|---|---|
| 拜访记录（VisitRecord） | `last-writer-wins`（按 `client_updated_at`）+ 保留冲突副本 | 逐项结果返回，部分成功可接受 |
| 计划停靠（PlanStop） | `reject-and-conflict-record` | 计划不可变，冲突记录转人工 |
| 客户资料（Customer） | `merge`（字段级，服务端优先非空） | 冲突字段记 `conflict_record` |

同步返回**逐项结果**：`[{item_id, status: applied|conflict|rejected, server_version}]`。

---

## 11. 安全与多租户隔离规范 v1.0【新增（M5，安全红线）】

### 11.1 数据库级 RLS（强制）

- 所有组织级表：`ALTER TABLE t ENABLE ROW LEVEL SECURITY;` + `ALTER TABLE t FORCE ROW LEVEL SECURITY;`
- 策略：`CREATE POLICY tenant_isolation ON t USING (organization_id = current_setting('app.current_org', true)::uuid) WITH CHECK (organization_id = current_setting('app.current_org', true)::uuid);`

### 11.2 会话绑定与连接池安全

- 应用以**非表 owner、非超级用户**角色连接（owner 默认绕过 RLS，故必须 `FORCE`）。
- 每个事务开始：`SET LOCAL app.current_org = '<org_uuid>'`（`SET LOCAL` 作用域限事务，防止连接复用串租户）。
- 后台任务/迁移显式设置 GUC；缺失 GUC 时策略返回空（fail-closed）。

### 11.3 绕过路径封堵

视图伪过滤、`SECURITY DEFINER` 函数、owner 连接均为常见绕过点：①禁止用视图替代 RLS；②`schema_migrations`/管理操作用独立角色；③ERD 与迁移评审纳入"是否启用 RLS"检查项。

### 11.4 PII 与合规

PII 数据字典（护照号、证件有效期、客户联系人联系方式、支付信息）分级 `SENSITIVE/RESTRICTED`；加密存储或脱敏展示；留存期限与删除（forget）接口；访问审计。

### 11.5 安全验收

测试用例：组织 A 会话读写组织 B 的 Trip/客户/计划/记忆，**必须全部失败**（见第 16 章 AT-S01）。

---

## 12. 数据模型与迁移【保留 + 新增】

### 12.1 三层数据（原文 31849–32632，【保留】）

- 核心业务层：Trip / Place / PlanVersion / PlanStop / Customer / CustomerPlace
- 工作流层：WorkflowInstance / Approval / SyncOperation
- 扩展集成层：Evidence / IntegrationConnection / ExternalAction

核心不变量【保留】：Trip 1:N PlanVersion 但**只能有一个 ACTIVE**；`trips(id, organization_id, version, active_plan_version_id)`；PlanVersion 状态 `SUPERSEDED/ACTIVE/DRAFT`。

### 12.2 迁移批次【修订：新增 0008–0013】

```text
0001_core_identity        # 组织、用户、成员
0002_trip_domain          # trip / place / customer / customer_place
0003_planning             # plan_version / plan_stop（不可变）
0004_execution_records    # visit_record
0005_workflows            # workflow_instance / approval / sync_operation
0006_reliability          # evidence / integration_connection / external_action
0007_security_indexes     # 索引与约束
0008_reliability_core     # outbox_events / background_jobs / idempotency_records
0009_proactive_runtime    # proactive_triggers / proactive_suggestions / suggestion_audit
0010_agent_memory         # traveler_profiles / plan_attempt_log / trip_memory_summaries（pgvector）
0011_llm_invocations      # llm_invocations
0012_metrics              # metric_snapshots
0013_security_rls         # ENABLE/FORCE RLS + 策略（全部组织级表）
```

约束【强化】：每个迁移可追踪、可在干净库重复构建、纳入 CI；迁移不可变（改动 = 新迁移）。

---

## 13. API 契约与统一约定【保留 + 强化】

### 13.1 五项强制规则（原文 7851+，【保留】）

①统一错误结构；②统一分页；③所有写操作可审计；④所有组织级资源强制租户上下文；⑤契约以 `contracts/openapi.yaml` 为唯一真相源。

### 13.2 核心资源与端点（节选）

| 方法 | 路径 | 幂等 | 说明 |
|---|---|---|---|
| POST | `/v1/trips` | 是（Idempotency-Key） | 创建多国 Trip |
| GET | `/v1/trips/{id}` | — | Trip 详情（含 active plan） |
| POST | `/v1/trips/{id}/imports:parse` | 是 | Excel 导入第一阶段（解析+预览+校验报告） |
| POST | `/v1/trips/{id}/imports:confirm` | 是 | Excel 导入第二阶段（确认落库） |
| POST | `/v1/trips/{id}/plans:generate` | 是 | 请求生成候选计划（返回 job_id） |
| GET | `/v1/jobs/{job_id}` | — | 查询后台任务状态 |
| GET | `/v1/trips/{id}/plans/{plan_id}` | — | 计划详情 + 验证报告 |
| GET | `/v1/trips/{id}/plans/{plan_id}:diff?against={other}` | — | 版本差异比较 |
| POST | `/v1/trips/{id}/plans/{plan_id}:activate` | 是 | 激活计划（乐观并发：`If-Match: version`） |
| POST | `/v1/visits/{id}/records` | 是 | 现场拜访记录（离线可批量同步） |
| POST | `/v1/sync:push` | 是 | 离线批次同步（逐项结果） |
| GET | `/v1/proactive/suggestions?trip_id=` | — | 主动建议列表 |
| POST | `/v1/proactive/suggestions/{id}:approve` | 是 | 人审通过 |
| POST | `/v1/proactive/suggestions/{id}:dismiss` | 是 | 驳回（进入误报回收） |

### 13.3 错误结构（【保留】）

```json
{"error": {"code": "PLAN_VERSION_CONFLICT", "message": "...", "details": {...}, "trace_id": "..."}}
```

---

## 14. 外部集成能力矩阵【新增（S1）】

| 能力 | 提供方类型 | 覆盖 | 更新频率 | 降级行为 | 人审边界 |
|---|---|---|---|---|---|
| 航班/交通 | GDS / NDC（Sabre/Amadeus/TravelSky 类） | 全球 | 实时 | 用最近缓存 + 标记 `stale` | 不改签、不预订 |
| 签证/入境要求 | 官方/聚合 | 多国 | 日/周 | 用已收录规则 + 标记需人工确认 | 只生成要求与清单，不自动提交 |
| 汇率 | 公开 API | 全球 | 每小时 | 用最近缓存 | 仅用于费用估算 |
| 天气/中断 | 公开 API | 全球 | 小时 | 忽略外部触发 | 仅提示 |
| 差旅政策 | 企业配置 | 组织内 | 变更时 | 用最近版本 | 仅校验与提示 |

原则【强化】：任一外部源不可用 ⇒ 系统必须**降级但可用**，并在结果中标记数据新鲜度；不得因外部不可用而伪造数据。

---

## 15. 可观测性规范 v1.0【新增（S3）】

- **结构化日志**：JSON，必含 `trace_id / org_id / trip_id / usecase / latency_ms`。
- **链路**：一次用户动作或一次主动触发贯穿 `trace_id`，覆盖 API → 用例 → 领域 → 规划 → LLM → DB。
- **审计**：`approvals` / `suggestion_audit` / `llm_invocations` / Outbox 事件全部留存，支持按 Trip 回放。
- **告警**：DLQ 新增、任务成功率低于 SLO、规划降级率异常、RLS 拒绝异常升高。
- **SLO**（见第 16 章）纳入监控。

---

## 16. 指标与验收规范 v1.0【新增（M6）】

### 16.1 指标树

| 维度 | 指标 | 目标 |
|---|---|---|
| 规划质量 | 约束满足率 / 验证器一次通过率 / 计划激活率 | ≥ 99% / ≥ 90% / 记录基线 |
| 主动式价值 | 主动建议采纳率 / **误报抑制率** / 冲突提前发现时长 / 时间节省估算 | ≥ 30% / ≤ 20% 误报 / ≥ 24h 提前 / 记录基线 |
| 可靠性 | 后台任务成功率 / 幂等命中率 / DLQ 率 | ≥ 99% / 记录 / ≤ 1% |
| AI 质量 | LLM 解释采纳率 / 降级率 | 记录基线 / ≤ 5% |

### 16.2 SLO

规划 P95 ≤ `solver_budget_seconds + 3s`；后台任务成功率 ≥ 99%；主动建议日打扰 ≤ `daily_cap` 达标率 ≥ 99%；记忆检索 P95 < 100ms。

### 16.3 验收场景（真实客户端—后端—数据库，不得以静态页面/模拟 API/预置数据替代）

**MVP-1 闭环 AT-001~AT-012【保留】**：创建多国 Trip → Excel 导入客户 → 校验客户/地址/坐标 → 设置商业价值/优先级/时间窗口 → 请求 AI 生成候选行程 → 查看地图/顺序/交通时间/费用/约束验证 → 增删调整拜访 → 生成新计划版本并与旧版比较 → 确认激活 → 现场记录（离线可存）→ 网络恢复同步 → 生成会议纪要及跟进任务草稿。

**新增主动式验收 AT-P01~AT-P04【新增】**：

| 编号 | 场景 | 通过标准 |
|---|---|---|
| AT-P01 | 出发前签证时效触发 | 系统在无用户请求下产生 `VISA_REQUIREMENT_REMINDER` 建议，含理由与证据，`requires_approval=true` |
| AT-P02 | 重复触发抑制 | 同 `dedupe_key` 反复触发只产生 1 条建议；超 `daily_cap` 时仅入库不推送 |
| AT-P03 | 人审 → 受控执行 → 审计 | 未审批的建议不可执行；审批后由业务服务执行并落审计链 |
| AT-P04 | 驳回 → 误报回收 | 驳回的建议标记 `dismissed`，同类建议权重下降（可观测） |

**新增安全验收 AT-S01【新增】**：组织 A 会话读写组织 B 数据（Trip/客户/计划/记忆）全部失败。

---

## 17. MVP-1 执行范围与里程碑【保留 + 修订】

### 17.1 交付范围（原文 37314–38017，【保留】）

后端业务闭环 + 规划引擎与独立验证器 + 计划版本与激活 + 现场记录与离线同步 + 主动式运行时（本次补强）+ Agent 记忆与复盘（本次补强）+ iOS SwiftUI 客户端 + 真实 PostgreSQL 集成测试。

### 17.2 里程碑【修订】

| 里程碑 | 内容 | 验证 |
|---|---|---|
| MS-1 | 仓库骨架 + PostgreSQL/PostGIS + 迁移 + RLS | 干净库可重复构建；AT-S01 通过 |
| MS-2 | 身份/组织/Trip/客户/地点 + Excel 两阶段导入 | AT-001~AT-004 |
| MS-3 | 规划引擎 + 独立验证器 + Outbox/Worker | AT-005~AT-007；确定性重放测试 |
| MS-4 | 计划版本/差异/激活 + 并发冲突 | AT-008~AT-009 |
| MS-5 | 现场记录 + 离线同步 + 主动式运行时 + 记忆层 | AT-010~AT-012、AT-P01~P04 |
| MS-6 | iOS 客户端 | 构建 + 单元测试通过 |
| MS-7 | 交付校验 | 全量测试 + 交付验证 |

---

## 18. 版本与演进治理【新增（S5）】

- API：`/v1` 前缀；破坏性变更升版；废弃提前 ≥ 1 个迭代通知。
- 迁移：不可变，向前兼容，破坏性变更走"双写/回填/切换"三步。
- **ADR**：`docs/decisions/ADR-xxxx.md`（编号、背景、决策、后果、状态）；本文关键决策（RLS、Outbox、pgvector 同库、OR-Tools+启发式、主动式边界）各留一条 ADR。
- 设计文档版本：v1→v2 变更在本文"追溯矩阵"与评审报告中留痕。

---

## 19. 风险与红线【保留 + 强化】

| 红线 | 落地位置 |
|---|---|
| RL-01 安全评审 | 第 11 章 + AT-S01 |
| RL-02 不可逆变更需回滚 | 计划版本化（SUPERSEDED）+ 迁移不可变 |
| RL-03 第三方依赖验证 | OR-Tools / pgvector 需 POC |
| RL-04 重构需测试覆盖 | 第 16 章测试集 |
| RL-05 无硬编码敏感信息 | 第 7.1 章 |
| RL-07 新服务须监控 | 第 15 章 |
| RL-08 辩论先独立分析 | 评审报告按此执行 |
| RL-10 不以时间不够跳过测试 | 第 17 章验收 |

---

## 20. 待人工确认节点

1. **主动式边界**：维持"只建议 + 准备，对外动作人审"（默认，与 7 项暂不承诺一致）。
2. **求解器双轨**：OR-Tools 优先 + 自研确定性启发式兜底（默认）。
3. **记忆落地**：同库 `pgvector`（默认，满足同事务边界）。
4. **向量扩展可用性**：若目标 PostgreSQL 实例不可装 pgvector ⇒ 降级为"关键词检索 + 结构化事实"，语义检索能力标记为受限（不伪造）。