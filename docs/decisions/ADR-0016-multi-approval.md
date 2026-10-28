# ADR-0016：审批策略支持多人会签

- 状态：已接受（Accepted）
- 日期：2026-10-28
- 相关文件：迁移 `0021_multi_approval`、`app/models/approval.py`、`app/services/approval_policy_service.py`、`app/services/proactive_service.py`
- 相关需求：审批矩阵/企业协作

## 背景

原审批策略只能要求"最低角色"，且 `approvals` 对同一对象只允许一条记录，无法表达"两个 ADMIN
都批准才执行"。企业客户需要按风险等级配置会签人数，同时默认单级流程不能被破坏。

## 决策

1. `approval_policies.required_approvals`（默认 1）；策略匹配时同时取最高角色与所需人数。
2. `approvals` 唯一约束从"每对象一条"改为"同一对象同一审批人一条"（部分唯一索引）。
3. 批准流程：
   - 每个审批人一条决策；
   - 不同审批人数量达到阈值才执行；
   - 未达阈值时建议保持 `PENDING`，响应返回 `executed=false / approval_count / required_approvals`；
   - 同一审批人重复批准幂等，不重复计数。
4. 达到阈值后才创建 `ExternalAction` 与 EXECUTED/AUDITED 审计；此前只记 APPROVED 决策。
5. 默认策略 `required_approvals=1`，既有单级流程与测试保持不变。

## 后果

- 好处：风险分级 + 多人会签可配置；审批链可审计到每个审批人。
- 代价：客户端 Today 页在未达阈值时仍显示 PENDING（需要刷新/等待其他审批人）；审批人通知仍由通知通道负责。
