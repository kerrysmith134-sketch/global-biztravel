# ADR-0008：审批策略按风险等级要求最低角色（单级优先）

- 状态：已接受（Accepted）
- 日期：2026-10-28
- 相关文件：迁移 `0013_approval_policies`、`app/models/approval.py`、`app/services/approval_policy_service.py`、`app/api/routes/admin.py`、`app/services/proactive_service.py`
- 相关需求：N1（审批矩阵抽象）

## 背景

N1 在设计文档里被列为"为后续企业多级审批预留，不在 MVP-1"。但高风险主动建议（如签证/航班
变化）只要求"有 TRAVELER 权限即可批准"，对 Org 管理员来说权限过低。完全不做策略，等未来
再加重构，会让 `approvals` 记录缺乏可配置的权限边界。

## 决策

1. 新增 `approval_policies`：按 `(organization_id, subject_type, min_severity)` 唯一，配置
   `required_role` 与 `enabled`。
2. 批准建议时计算匹配策略：`severity >= min_severity` 的策略中取最高 `required_role`；
   没有匹配策略 ⇒ 不额外限制（保持既有行为）。
3. 角色门在服务层执行（不是路由），HTTP 与内部调用共用；拒绝返回 403 并给出 required/actor。
4. 不实现多人会签/审批链：`approvals` 仍是单条决策；多级审批需要"决策明细表 + 满足条件"，
   留到企业版，不提前做半成品。

## 后果

- 好处：风险分级可配置；默认零策略不改变现有流程；测试可注入临时 TRAVELER 验证拒绝。
- 代价：策略只覆盖"最低角色"，不能表达"两个 ADMIN 同时批准"这类规则。
