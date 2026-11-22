# ADR-0039：原子主张核验只能生成暂定结论，不得自动认证来源独立性

- 状态：已接受
- 日期：2026-11-22
- 关联：ADR-0035、ADR-0037、ADR-0038；市场情报引擎 H1.9
- Supersedes：无

## 背景

H1.7 的 Research Answer Agent 会为答案版本关联 SUPPORTS / CONTRADICTS / CONTEXT Evidence，但这是答案级证据映射。一个答案可能混合多个可独立判断的事实，粗粒度关系无法指出具体哪条证据支持或反驳哪一个主张。

H1.8 补充了用户选定网页的受限正文摘录，但抓取成功不代表内容真实；两个不同域名也不代表两个独立出版方，转载与联合发布会造成伪独立佐证。

## 决策

1. **答案版本作为输入边界**：只有用户对某一 MarketResearchQuestionAnswer 版本显式触发核验时，Agent 才读取该答案及其已关联 Evidence。Agent 不自动扩大来源集合、不自动搜索或抓取新网页。
2. **原子主张拆分**：Agent 将答案拆分成 FACT、ESTIMATE、OPINION、FORECAST、INFERENCE 等主张类型，保留对象、地域、时间和条件等 scope；不得将建议、推测或意见伪装为事实。
3. **主张级证据映射**：每条 Claim 单独关联 SUPPORTS、CONTRADICTS 或 CONTEXT Evidence。所有 evidence_id 必须属于当前答案快照；服务端拒绝/丢弃不存在、重复或未输入的 ID。
4. **服务器确定性裁决**：LLM 只提供候选主张和关系。最终暂定 verdict 与 confidence 由服务端按关系计数、Evidence 核验状态和来源信息重新计算：
   - 同时有支持和反驳 → CONFLICTED；
   - 只有支持 → PROVISIONAL_SUPPORT；
   - 只有反驳 → POTENTIAL_CONTRADICTION；
   - 没有明确关系 → INSUFFICIENT。
5. **不自动认证来源**：不同 host 只能记录为 DISTINCT_HOST_UNCONFIRMED；相同 host 与相同 content hash 可用于标记重复或同源。不同域名/不同哈希均不能证明出版方相互独立。H1.9 的 `confirmed_independent_source_count` 固定为 0。答案采纳中的 `independent_sources` 验收条件只统计去除同 host/相同内容后的候选来源组；达到数量后仍必须由管理员人工核查出版方归属、所有权及转载关系，并将审查结论写入审核说明。系统不得把候选来源组数量描述为已证实的独立来源数量。
6. **不输出机器 VERIFIED**：主张 verdict 只能是暂定支持、潜在反驳、冲突或证据不足。Agent 不设置 Evidence.verification_status，不输出 HIGH confidence。即使出现多个 VERIFIED Evidence host，也最多给 MEDIUM，并继续明确独立性尚未确认。
7. **人工采纳与事实核验分离**：管理员可以采纳/驳回主张。ACCEPTED 表示有人审核并接受该主张用于产品工作流，不代表机器或数据库把它认证为 VERIFIED；所有审核必须记录审查人、时间和理由。数据库 CHECK 同时限制 ACCEPTED 主张必须为 PROVISIONAL_SUPPORT、有支持 Evidence、无反驳 Evidence，并具备非空审核理由。答案级 `independent_sources` 条件的候选来源数量也不能替代人工独立性审查。数据库禁止应用角色 UPDATE/DELETE/TRUNCATE 主张版本；只允许更新状态与审核字段。主张正文、范围、分类、核验结论、置信度和计数在创建后不可修改。数据库 CHECK 进一步保证只有 PROVISIONAL_SUPPORT、至少一条支持且没有反驳的主张才能被写为 ACCEPTED，ACCEPTED/REJECTED 都必须有审核人、时间和非空理由。
8. **版本化和审计**：每次核验保存输入摘要、Prompt 版本、Workflow、AgentRun、LLMInvocation、Claim 版本、Evidence 内容哈希/核验状态快照与活动事件。重复输入可幂等复用完成的运行。
9. **复用现有执行基础设施**：只新增 Claim 领域对象，不创建新队列、Agent Runtime、搜索适配器或证据主表；复用 BackgroundJob、WorkflowInstance、MarketWorkspaceWorkflow、AgentRun、Evidence 与 LLM Gateway。

## 后果

- 市场研究结果从“答案带引用”提升为“每个原子主张都有单独的证据关系、冲突状态和版本记录”。
- H1.6 的答案采纳规则同步收紧：候选来源组由 host 与内容指纹聚类；候选组达到门槛只说明“有待人工检查的来源集合”，仍需管理员在审核说明中明确记录出版方、所有权和转载关系审查。
- 证据不足与相互矛盾会显式保留，不会被一个整体答案的置信度掩盖。
- 来源独立性仍是一个未完成的研究问题；H1.10 需要做出版方/转载/内容谱系核验和人工来源关系审核，不可用 host 去重替代。
- Agent 的 CLAIM verdict 是证据映射与审查优先级建议，不是法律、财务或其他高影响领域的权威事实认证。
