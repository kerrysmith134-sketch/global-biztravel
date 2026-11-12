# 性能、容量与成本预算（S2）

对应设计文档 v2.0 第 16 章 SLO 与第 17 章 MVP 范围。本文给出**可执行的预算假设**与**已测得的
本地基准**；生产容量需要按真实流量重新校准。

## 1. 容量假设（MVP-1 单部署）

| 维度 | 假设 | 说明 |
|---|---|---|
| 组织数 | 50 | 单库多租户；RLS 强制 |
| 用户数 | 500 | 平均每组织 10 人 |
| 出行（Trip） | 5,000 / 年 | 每人每年约 10 次 |
| 拜访（Visit） | 50,000 / 年 | 每次出行 10 个 |
| 计划版本 | 20,000 / 年 | 每次出行约 4 版 |
| 现场记录 | 100,000 / 年 | 每拜访 2 条（含离线重试） |
| 主动建议 | 50,000 / 年 | 每出行约 10 条（含抑制） |
| 记忆摘要 | 5,000 / 年 | 每完成出行 1 条 |

## 2. 时间预算

| 环节 | 预算 | 实现约束 |
|---|---|---|
| 普通读接口（列表/详情） | P95 ≤ 300ms | 分页 + 批量计数，禁止 N+1 |
| 计划求解 | `solver_budget_seconds`（默认 10s），HTTP 不等待 | 异步 job + Worker；超时/降级标记 |
| 计划接口（含排队） | P95 ≤ 求解预算 + 3s | `GET /v1/jobs/{id}` 轮询 |
| 记忆混合检索 | P95 < 100ms（租户内） | 两路召回各 `limit*4`，RRF + 时间衰减 |
| 主动式扫描 | 每分钟一轮（可配） | 单租户全量在途出行；规则彼此隔离 |
| 通知投递 | 每轮批量 50 | Outbox + 通知状态机，失败可重试 |
| 指标快照 | 每小时 | 同窗口覆盖写 |

## 3. 本地基准（2026-11-10 实测，Apple Silicon，`make bench`）

N3 基准集：`packages/planning-engine/tests/instance_family.py` 的 8 个具名实例，
覆盖七个难度轴（规模/走廊簇拥、规模×每日容量、星形放射、洲际长窗、时间窗紧度、
锁定顺序、夏令时切换日、不可行）。坐标由真实城市锚点算术插值，无随机源；目标值的
**金目标基线**在 `tests/regression_baselines.json`，求解器行为漂移即门禁失败。

2026-11-10 实测（启发式 20 轮取 p50，ortools 1 轮）：

| 实例 | 轴 | heuristic p50 | ortools | 覆盖 |
|---|---|---|---|---|
| corridor_rhine_8 | 规模/走廊 | 0.8ms | 494ms | 8/8 |
| corridor_rhine_12 | 规模×容量 | 1.4ms | 拒绝（规模护栏） | 12/12 |
| star_china_10 | 地理/星形 | 1.8ms | 7.5s | 10/10 |
| global_air_10 | 洲际/长窗 | 1.3ms | 6.2s | 10/10 |
| tight_windows_8 | 时间窗紧度 | 诚实失败 | 22ms | 8/8 |
| locked_order_10 | 锁定顺序 | 1.1ms | 8.9s | 10/10 |
| dst_spring_berlin_6 | 夏令时 | 诚实失败 | 6.5ms | 6/6 |
| infeasible_window_4 | 诚实失败 | SolverFailed | SolverFailed | — |

四个实测发现（S2 语义校准）：

1. **`solver_budget_seconds` 不是墙钟预算**。OR-Tools 刻意不用 `time_limit`
   （跨机器不确定），改用"解数量上限"（预算×40 个解，见
   `ortools_solver.SOLUTION_LIMIT_PER_BUDGET_SECOND`）。每个解的搜索成本随
   规模增长，且 DFS+GUIDED_LOCAL_SEARCH 在 n≥12 后墙钟呈指数级：12 站走廊
   单次 39s、16 站可达分钟级。**治理（ADR-0031）**：ortools 入口按问题内容
   封顶 `ORTOOLS_MAX_ELIGIBLE=10`，超出即拒绝、流水线落启发式
   （`degraded=true`）——把指数因子钉死在护栏内，`solver_budget_seconds`
   退为线性调节旋钮。corridor_rhine_12 由此从"ortools 39s"变为"入口拒绝"，
   金目标基线记 null。
2. **墙钟本身不可作为预算**。同一实例（global_air_10，同预算）2026-11-10 实测
   6.2s、2026-11-12 高负载实测 253s——同机不同负载差 40 倍。这既坐实了
   "不用墙钟 time_limit"的既有设计，也意味着护栏封规模之外，剩余的
   budget×机器负载波动只能靠异步 job（HTTP 不等待）吸收。
3. **两求解器能力有真实边界**。窄时间窗（tight_windows_8）与夏令时切换日
   （dst_spring_berlin_6）实例上，贪心启发式排不进 eligible（诚实失败），
   只有 ortools 可解；实例族用 `min_solver` 声明这一边界并纳入门禁。
4. **目标值不保证一致**。star_china_10 上 ortools(1381.45) 优于启发式(1481.45)，
   其余实例两者一致——与"不互相断言最优性"的既有约定一致，金目标按求解器
   分别记录。护栏溢出到启发式的质量损失实测有限（族内可解实例仅此 1 例有差，
   差距 ~7%）。

> 回归门禁四类：覆盖 / 能力声明（`min_solver` 与 `kind="infeasible"` 的失败语义）/
> 确定性（`problem_hash` 跨轮一致）/ 金目标基线。OR-Tools 求解失败或未安装时
> 流水线沿兜底链降级并标 `degraded=true`。

## 4. LLM 成本预算

- 每次会议纪要草稿：输入约 300–800 tokens、输出 200–400 tokens；按单价
  `GBT_LLM_COST_PER_1K_TOKENS` 估算，写入 `llm_invocations.cost_estimate`。
- 相同输入命中 `input_hash` 缓存：`cached=true`，token/成本记 0；缓存命中率可从
  `GET /v1/metrics/summary` 的 `ai.cache_hit_rate` 观察。
- 预算护栏：`GBT_LLM_MAX_TOKENS`；超预算/不可用 ⇒ 模板降级，`degraded=true`。

## 5. 扩容触发点与顺序

1. **指标/Outbox 表增长**：先加时间分区/归档（按 `created_at`），再考虑独立存储。
2. **记忆检索变慢**：为 `trip_memory_summaries.embedding` 建 HNSW 索引（当前数据量小，seq scan
   足够；建索引前需回填真实 embedding）。
3. **Worker 吞吐不足**：增加 Worker 进程即可（任务 `SKIP LOCKED` + 租约已保证并发安全）。
4. **主动式扫描成本**：按组织/出行窗口过滤，或把扫描拆成独立任务队列。
5. **读接口压力**：读副本 + 分页；RLS 仍在副本上生效。

## 6. 回归方式

```bash
make bench          # N3 实例族：耗时/覆盖/能力声明/金目标基线（ortools 默认 1 轮，约 1 分钟）
make bench ARGS="--skip-ortools"   # 只跑启发式（秒级）
make test           # 全量行为测试（含 test_benchmark_family.py 的 38 条实例族回归）
make e2e            # 真实 HTTP + DB + Worker 全链路
.venv/bin/python scripts/gen_regression_baselines.py --check   # 金目标漂移校验
```

求解器行为变化（哪怕看似改进）导致金目标漂移时：确认变更有意 → 重新生成基线
（去掉 `--check`）→ 在提交说明里写明原因。与契约样本门禁（ADR-0030）同一治理方式。
