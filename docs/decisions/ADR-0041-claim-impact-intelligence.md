# ADR-0041：主张版本变化采用只读下游影响分析

- 状态：已接受
- 日期：2026-11-22
- 关联：ADR-0039、ADR-0040；市场情报引擎 H2.9

## 背景

H2.8 建立了跨研究答案版本的原子主张谱系，但市场判断（Insight）与商业机会（Opportunity）目前关联的是原始 Evidence，而不是某一条 Claim 版本。当同一 Claim 出现新版本时，消费者可能继续引用旧 Evidence；既有系统既不能指出哪些下游对象与该 Claim 共享证据，也不应仅凭机器生成的新版本就自动撤销人工审核过的业务判断。

## 决策

1. **只从现有证据关系推导影响**：以 `MarketClaimEvidence` 中的 Evidence ID 为桥，查找同一 Workspace 内 `MarketInsightEvidence` 与 `MarketOpportunityEvidence` 对相同 Evidence ID 的引用。不使用标题、自然语言相似度或模型猜测建立隐式依赖。
2. **区分待审核版本与已采纳版本**：最新 Claim 处于待审核、冲突或证据不足状态时，提示关联消费者重新检查；最新 Claim 已采纳且消费者仍引用未出现在最新版本中的旧 Evidence 时，提示旧证据快照可能过期。
3. **保留当前人工结论**：影响报告是只读派生视图，不改写 Insight 状态、Opportunity 阶段、资格、负责人、业务结果、Claim 审核状态或 Evidence。
4. **租户与 Workspace 隔离**：所有查询同时限定 organization_id 与 workspace_id；只查询消费者当前仍有效的 Insight/Opportunity，排除已驳回、已替代和已关闭对象。
5. **明确边界与上限**：报告最多分析 200 条 Claim 谱系、5000 个历史 Claim 和 250 条影响关系。超出时返回 `truncated=true` 与分析/总量，不能把截断结果描述为完整清单。
6. **不宣称语义因果**：共享 Evidence ID 是可复核的显式数据关联；它说明来源被共同引用，不证明某一 Insight 或 Opportunity 必然由该 Claim 推导，也不证明结论错误。

## 后果

- 使用旧来源快照的市场判断和机会可以被主动发现，不必等到用户手动逐条对照 Claim 历史。
- 新版 Claim 待审核期间，旧版已采纳事实不会被静默撤销。
- 当前阶段不新增多态依赖表或自动失效状态；只有用户确认实际业务依赖模型后，才考虑建立显式 Claim-to-Consumer 关系及其审查工作流。
