# ADR-0038：来源网页提取必须由用户点选且默认关闭

- 状态：已接受
- 日期：2026-11-22
- 关联：ADR-0035、ADR-0037；市场情报引擎 H1.8
- Supersedes：无

## 背景

H1.5 的 ResearchPlan Worker 已将搜索结果标题与摘要保存为 Workspace Evidence，但搜索摘要不是原始网页全文，也不能据此确认事实。H1.7 的答案 Agent 只读取 Evidence 快照，并把未核验来源限制为非 HIGH 置信度。

下一步需要提供来源正文摘录，以便后续研究与独立核验有更充分的材料；但自动遍历搜索结果会产生不必要的外网请求、隐私与 SSRF 风险，也会把用户并未选择的站点纳入处理范围。

## 决策

1. **显式选择**：仅允许用户对当前 Workspace 中的一条 `WEB_SEARCH_RESULT` Evidence 发起提取。搜索 Worker 和 Research Planner 不自动抓取搜索结果；被驳回、没有 URL、非搜索结果或其他 Workspace 的 Evidence 均不可提取。
2. **默认关闭**：网页提取 Provider 默认 `unavailable`。部署方必须通过配置显式启用 `safe_http`，不能因安装代码就默认发出公网请求。
3. **SSRF 防护**：只允许 HTTP 80 与 HTTPS 443；拒绝 URL userinfo、非公网 IP、DNS 返回的任意非公网地址及非标准端口；每次重定向必须重新校验 URL 和 DNS，限制重定向次数，禁止 HTTPS 降级；TCP 连接固定到已校验 IP，TLS 证书验证与 SNI 保持原主机名。
4. **资源边界**：设置连接超时、响应字节数、可接受媒体类型、最大重定向数与摘录字符数。不能执行网页脚本，不运行浏览器，也不保存整页 HTML。
5. **最小留存**：保存受限正文摘录、规范化全文 SHA-256、字符数、抓取时间、最终 URL、可观察到的标题/作者/日期元数据与提取器版本。全文哈希用于内容版本标识，不代表事实正确性。
6. **不可变血缘**：从原始搜索 Evidence 创建新的 `WEB_PAGE_EXTRACT` Evidence；不覆盖来源摘要。新增 `MarketEvidenceDerivation` 记录来源 Evidence、派生 Evidence、Workspace、Workflow、AgentRun、提取器版本和内容哈希。派生关系为 append-only。
7. **不升级事实状态**：提取后的 Evidence 永远从 `UNVERIFIED` 开始。HTTP 200、网页标题、发布日期、全文内容和哈希都不能单独使 Evidence 变为 PARTIALLY_VERIFIED 或 VERIFIED。
8. **执行模型**：复用现有 WorkflowInstance、MarketWorkspaceWorkflow、BackgroundJob、AgentRun、TASK_HANDLERS、重试/DLQ 与 MarketActivityEvent，不引入第二套队列或 Agent Runtime。

## 后果

- 用户能针对一条具体搜索来源补充网页正文，而不会触发对整个搜索结果集的自动抓取。
- 原始摘要与网页派生版本可分别追溯；版本 hash 基于规范化全文，而 Evidence payload 只保存限长 excerpt。
- 默认关闭和 DNS pinning 降低 SSRF 面，但仍需在部署网络层限制 Worker 的出站访问；应用级校验不能替代 egress firewall。
- 仅基于 HTTP 状态、页面文本和元数据不能完成事实核验。后续 H1.9 必须引入独立来源比较、主张级证据映射与冲突处理，且保留人工采纳边界。
