# ADR-0027：入境评估陈述证件前提，不给确定性结论

- 状态：已接受（Accepted）
- 日期：2026-11-02
- 相关文件：迁移 `0030_traveler_nationality`、`app/models/memory.py`（`TravelerProfile.nationality`）、
  `app/services/visa_service.py`（`traveler_nationality`/`assess_entry`）、`app/api/routes/memory.py`
- 相关需求：PRD P0-7「结合用户实际证件、国籍、签证和过境情况检查可行性」「对信息不足主动提示」、方向二红线

## 背景

`assess_entry` 此前只按国别查离线目录，完全不知道评估对象持哪国护照——同一个目的地对
不同国籍的入境要求可能相反。PRD 要求结合证件与国籍，同时明确"签证规则属于高风险信息，
AI 不能仅凭常识给出确定的入境结论"。

## 决策

1. **国籍落在事实记忆层**：`traveler_profiles.nationality`（`char(2)` + 正则 CHECK），
   走既有画像版本化写入（内容不变不升版本；请求里 `nationality` 字段出现且为 null 才清除，
   与"没提"区分）。`PUT /v1/traveler/profile` 接受大小写不敏感的两位字母并归一为大写。
2. **评估只陈述前提与缺口**：有国籍 → summary 前缀"证件前提：XX 护照。"；无国籍 →
   "证件前提：未登记护照国籍，无法结合证件核验。" 两种情况都**不出现**"免签/需要签证"
   之类的确定性结论，`manual_review_required` 恒为 True（方向二红线）。
3. **缺信息即降置信度**：未登记国籍时 `confidence = min(原值, 0.35)`——评估对象不明的
   结论更不可靠，用数值如实表达，而不是照常给 0.6。
4. `traveler_nationality` 取**出差人（trip.owner_user_id）**最新有效画像版本，取不到返回
   `None`，绝不回退到默认国籍。

## 后果

- 好处：评估结论的适用对象可回溯（哪本护照、哪版画像）；缺证件信息在摘要与置信度上双重
  可见；接入按国籍差异的官方数据源时，只改 `assess_entry` 的前提拼装，不动存储。
- 代价：目录仍不含按国籍的免签/需签矩阵——这是**刻意的**（无官方源不编造）；要真正给出
  按国籍的可行性，需要后续接入官方/聚合数据源并保留来源与版本。
