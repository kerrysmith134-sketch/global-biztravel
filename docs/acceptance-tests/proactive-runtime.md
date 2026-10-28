# 主动式运行时验收（AT-P01 ~ AT-P04 + STATE）

对应设计文档 v2.0 第 16.3 章。全部用例打真实 PostgreSQL（容器 `zhi-tu-pg`），走
FastAPI 应用栈、RLS 会话与真实服务层，不使用模拟 API 或预置演示数据。

测试文件：`services/api/tests/test_proactive_acceptance.py`

```bash
make db-up          # 如未启动
.venv/bin/python -m pytest services/api/tests/test_proactive_acceptance.py -v
```

## 场景映射

| 编号 | 验证目标 | 测试用例 | 关键断言 |
|---|---|---|---|
| AT-P01 | 出发前签证时效触发 | `test_at_p01_scheduled_visa_trigger_proposes_with_evidence` | 48h 后出行的签证建议自动生成；`status=PENDING`、`requires_approval=true`、含 `TRIP_WINDOW` / `VISA_RULE_CATALOG` / `VISA_RULE` 证据、`rationale` 非空 |
| AT-P02 | 重复触发抑制 | `test_at_p02_same_dedupe_key_never_proposes_twice` | 同一 `dedupe_key` 连续扫描两次只产生 1 条建议、1 条触发；第二次 `deduped≥1` |
| AT-P02 | 每日打扰上限 | `test_at_p02_daily_cap_stores_but_does_not_push` | `cap=3` 时第 4 条建议 `push_status=WITHHELD`、`push_reason=DAILY_CAP`、`notified_at IS NULL`，且不出现在用户列表 |
| AT-P03 | 人审 → 受控执行 → 审计 | `test_at_p03_approval_executes_and_writes_audit_chain` | 批准前无审批记录、无执行结果；批准后 `approvals` 1 条、`status=EXECUTED`、执行结果 `PREPARED` 且明确「未对外」；审计链 `DETECTED→EVALUATED→PROPOSED→APPROVED→EXECUTED→AUDITED`；同幂等键重放字节一致且不重复审批 |
| AT-P04 | 驳回 → 误报回收 | `test_at_p04_dismiss_marks_false_positive_and_lowers_confidence` | 驳回后 `status=DISMISSED`、原因落库、`memory_feedback.weight_delta<0`；同类新建议置信度下降、`feedback_penalty>0` |
| STATE | 状态型触发 | `test_state_trigger_detects_missing_coordinates` | 无坐标地点产生 `PLACE_COORDINATES_MISSING`；触发记录 `type=STATE`、`rule_id=missing_coordinates` |

## 边界与已知限制

- **EXTERNAL 触发源未接入**：签证政策 / 汇率 / 天气的真实外部源属于设计第 14 章集成层，
  当前未建立连接。`visa_check` 只使用仓库内离线目录 `app/data/visa_catalog.json`，
  所有文案都写「需人工核验」，不宣布签证结论。
- **推送语义**：`PUSHED` 表示「进入用户可见的建议流」，当前没有 APNs / 邮件通道；
  `WITHHELD` 表示入库但不展示。两者分离是为了让「为什么没看到」可解释。
- **执行只准备不对外**：`approve` 后的执行结果全部是草稿 / 清单 / 待办，写入
  `proactive_suggestions.execution_result`；不发送、不预订、不提交签证。
- **测试卫生**：用例创建的出行在结束时删除，避免污染每日打扰上限；`approvals` 是通用表，
  清理时先按 `subject_id` 删除。
