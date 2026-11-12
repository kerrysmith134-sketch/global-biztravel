# ADR-0031 OR-Tools 规模护栏：按问题内容封顶 eligible 停靠数

- 状态：已接受（2026-11-12）
- 落点：`packages/planning-engine/travel_planner/solvers/ortools_solver.py`
  （`ORTOOLS_MAX_ELIGIBLE = 10`，`solve()` 入口拒绝）
- 实证来源：N3 求解器基准集（`tests/instance_family.py` + `make bench`，
  数据见 `docs/architecture/performance.md` §3）

## 背景

OR-Tools 求解器为满足跨机器确定性（设计文档 §6.4），刻意不使用墙钟
`time_limit`，改用**解数量上限**：`solution_limit = solver_budget_seconds × 40`
（`SOLUTION_LIMIT_PER_BUDGET_SECOND`）。N3 基准集实测发现该预算管不住真正的
风险因子：

| 规模（eligible） | 单解搜索成本（实测） | 单次求解墙钟 |
|---|---|---|
| n=8 | ~6ms | 0.02–0.5s |
| n=10 | ~150ms | 6–9s |
| n=12 | ~1s | 39s（budget=1，仅 40 解） |
| n=16 | 外推数秒/解 | 分钟级（budget=2 未能在 7 分钟内返回） |

即 DFS+GUIDED_LOCAL_SEARCH 的**单解成本随 n 指数增长**，`solver_budget_seconds`
只是线性旋钮；生产默认预算是 10（=400 解），n=12 的行程最坏可烧 **6.5 分钟**
CPU。更糟的是墙钟本身不可预测：同一实例（global_air_10）两天实测 6.2s → 253s
（同机不同负载差 40 倍）。

规划任务是异步 job（Worker 执行、HTTP 轮询），结果正确性不受影响，但容量不可
规划：一次 n=16 的求解就可能占住 Worker 十分钟以上，且方差极大。

## 备选方案

| 方案 | 判定 | 理由 |
|---|---|---|
| 不作为（依赖异步架构吸收） | 拒绝 | 指数尾部无界：n=16 外推分钟级、n=18 更久；Worker 池会被单任务长期占住 |
| 墙钟 `time_limit` | 拒绝 | 直接违反确定性要求（第 6.4 节）——不同机器/负载产出不同计划，`problem_hash` 缓存与重放全部失效 |
| 按 budget 缩减（大实例自动降 solution_limit） | 拒绝 | 指数因子在 n 不在 budget；budget→1 时 n=12 仍 39s，治标不治本 |
| **按问题内容封顶规模（本 ADR）** | **采用** | 护栏是纯函数判定（`len(eligible) > ORTOOLS_MAX_ELIGIBLE`），跨机器一致；把指数因子钉死在 n≤10，budget 退为线性旋钮 |

## 决策

1. `ortools_solver.solve()` 入口增加规模护栏：`len(eligible) > ORTOOLS_MAX_ELIGIBLE(=10)`
   时抛 `SolverFailed`（信息含 "scale guard"）——与"依赖缺失 ⇒ `SolverUnavailable`"
   同属求解器能力边界，在建模之前拒绝。
2. 流水线**不做任何改动**：护栏拒绝沿既有兜底链落到启发式，结果
   `solver=HEURISTIC`、`degraded=true`、仍须通过独立验证器全覆盖校验。
   显式指定 `solver=ORTOOLS` 的大实例同样走此路径（能力边界优先于显式选择）。
3. 质量损失显式接受：族内可解实例仅 1 例（star_china_10）ortools 更优，
   差距 ~7%；其余实例两求解器目标值一致。溢出到启发式不是质量回归，
   是回到既定的兜底解。
4. 金目标基线对超出护栏的实例记 `null`（`gen_regression_baselines.py` 显式跳过，
   防止把 `plan_trip` 兜底后的启发式目标值误记为 ortools 基线）。

## 后果

- **+** 求解墙钟获得确定性上界：n≤10 时最坏为 budget 线性（budget=10 约
  1–2 分钟量级、低负载秒级），容量可规划。
- **+** 语义诚实：拒绝是显式的、可测试的（`test_solvers.py` 三条护栏测试 +
  bench 裸求解器断言），不是静默变慢。
- **−** n>10 的行程失去 ortools 质量增益（实测有限，见决策 3）。
- **−** 护栏值 10 是经验值（2026-11 实测梯度），机器升级后可放宽；
  它是单一常量，调整须同步更新基线与 `performance.md` 数据。
- **中立**：计划结果带 `degraded=true` 时，客户端与审计已能区分
  "确定性启发式解"与"ortools 解"（既有字段，无契约变更）。
