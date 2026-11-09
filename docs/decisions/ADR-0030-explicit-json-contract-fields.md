# ADR-0030：契约里的自由 JSON 字段必须显式建模，并用契约样本门禁

- 状态：已接受（Accepted）
- 日期：2026-11-09
- 相关文件：`services/api/app/api/schemas.py`（`PlanUnplacedRead` / `DataGapRead` /
  `AssistantCardRead`，`CurrencyTotalRead` / `PolicyViolationRead` 复用）、
  `scripts/gen_ios_contract_samples.py`、`scripts/check_docs.py`、
  `apps/ios/GlobalTravelOSTests/ContractSampleTests.swift`、`Makefile`（`ios-contract-samples` / `ios-contract-check`）
- 相关需求：设计评审 S6「契约同步机制」、`contracts/openapi.json` 为唯一真相源的约定

## 背景

后端有 5 个响应字段在 Pydantic schema 里写成 `list[dict[str, Any]]`，但它们**实际结构是固定的**：

| 字段 | 实际结构 | 来源 |
|---|---|---|
| `PlanVersionRead.unplaced` | `{visit_id, reason, source, detail?}` | `planning_service._unplaced_payload` |
| `PlanVersionRead.policy_violations` | `{code, message, details}` | `policy_service.evaluation_payload` |
| `AssistantMessageRead.cards` | `{type, title, detail, entity_id?, link?, proposal_id?, payload?}` | `assistant_service` |
| `LocalGuideRead.data_gaps` | `{code, message, country_code?}` | `local_guide_service` |
| `TripOutcomeRead.actual_cost` | `{currency, amount, count}` | `trip_outcome_service` |

写成 `dict` 有两个后果：① 契约对客户端没有任何约束力（OpenAPI 里是自由对象），iOS 侧只能
靠"读后端代码猜结构"；② 现有 iOS 解码测试用手写 JSON，样本怎么写就怎么过，**契约漂移完全没有
信号**——契约写 `list[dict]`、iOS 模型却要求 `visit_id`/`currency`，这类错配在真实响应上会直接
抛解码错误。

## 决策

1. **实际结构固定的字段一律显式建模**：新增 `PlanUnplacedRead` / `DataGapRead` /
   `AssistantCardRead`，复用已有的 `CurrencyTotalRead` / `PolicyViolationRead`。
   契约里不再出现"业务字段用 `dict[str, Any]` 逃逸"。
2. **多态展示面允许额外字段透传**：助手卡片有多种 `type`（含 `TRIP_PROPOSAL` 的 `payload`、
   `ACTION_RESULT` 的 `proposal_id`）。`AssistantCardRead` 显式列出已知字段，并
   `model_config = ConfigDict(extra="allow")`——卡片新增字段不会被响应模型静默丢弃，
   也不会因漏列一个字段而让既有接口 500。
3. **用契约生成 iOS 解码样本**：`scripts/gen_ios_contract_samples.py` 读
   `contracts/openapi.json`，为 13 个核心读模型生成样本（JSON 嵌进
   `ContractSamples.generated.swift`），`ContractSampleTests` 用真实 `DateCoding.decoder()`
   解码。**契约改字段名/类型/枚举 → 样本变 → 解码测试红**，这是手写样本给不了的保护。
4. **门禁**：`make ios-contract-check` 重新生成样本并比对 git 差异（漂移即失败），接入
   `scripts/ci.sh` 与 GitHub workflow 的静态段；`make ios-contract-samples` 用于改契约后更新样本。

## 后果

- 好处：契约对 5 个字段重新有约束力；iOS 侧获得"契约→解码"的自动化检查，不再依赖人肉比对；
  收紧过程本身抓到了此前未暴露的结构假设（如 `TRIP_PROPOSAL.payload` 被漏列、卡片多态字段）。
- 代价：前端/客户端新增消费字段时，改契约要同步跑 `make ios-contract-samples` 并提交生成文件；
  生成器只覆盖 13 个核心读模型，不是全量 140 个 schema——按需扩展 `TARGETS` 即可。
- 边界：生成样本是"类型合法的最小响应"，不替代业务语义测试（语义仍由 `make e2e` 与各集成测试覆盖）。
