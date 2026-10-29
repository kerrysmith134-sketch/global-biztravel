# ADR-0022：通知等级在入队时判定并落库

- 状态：已接受（Accepted）
- 日期：2026-10-29
- 相关文件：迁移 `0026_notification_priority`、`app/services/notification_policy_service.py`、`app/services/notification_service.py`、`app/api/routes/notifications.py`、iOS `NotificationsView`
- 相关需求：PRD S8-08「主动通知与待办中心」（P0）

## 背景

PRD 要求通知分 P0（紧急）/P1（高优先级）/P2（一般）/P3（信息性）四级，并按业务影响、
时间紧迫性与用户偏好动态调整。系统此前所有推送样式一致，用户无法分辨"航班取消"与
"普通进度更新"的区别，也就无法把打扰预算花在刀刃上。

## 决策

1. 新增 `notifications.priority`（P0–P3，CHECK 约束）与 `issue_key`（建议 `dedupe_key`
   快照，用于同一问题聚合展示）。迁移 `0026`。
2. **入队时判定**并落库，不在读取时重算：等级是"当时为什么这么推"的证据，事后规则变化
   不应改写历史。
3. 确定性规则（`notification_policy_service.classify`，按序命中）：
   - P0：`FLIGHT_STATUS_ALERT` / `PLAN_UNPLACED_VISITS` 且风险 HIGH（可能错过重要预约）；
   - P1：签证/入境类 HIGH，以及其余 HIGH 风险；
   - P2：MEDIUM 风险（天气、汇率等可汇总项）；
   - P3：LOW 风险。
   未知类型与未知风险档退回中档，绝不静默降为最低打扰。
4. `GET /v1/notifications` 支持 `priority` 过滤（P0–P3），并在 Feed 里返回
   `unread_by_priority` 分级未读计数（口径与 `unread` 一致：已送达 + 未读）。
5. iOS Today 新增"通知"入口：按 P0→P3 分组、等级徽标、分级未读摘要与单条标记已读。
6. 去重、冷却、每日上限仍由既有建议抑制层负责——本策略只解决"响得多响"，不解决"响不响"。

## 后果

- 好处：打扰分级可审计；接入真实航班源时 P0 规则已经就位，不需要临场拍板；过滤与未读
  分级让 P2/P3 可以汇总浏览而不淹没 P0/P1。
- 代价：等级是**入队快照**，用户偏好调整不会追溯改写历史通知；"根据用户偏好动态调整"
  仍是后续项（当前只有全局规则，没有 per-user 偏好表）。
