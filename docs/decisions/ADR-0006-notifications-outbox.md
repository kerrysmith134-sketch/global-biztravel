# ADR-0006：推送通道走 Outbox，未配置的通道显式失败

- 状态：已接受（Accepted）
- 日期：2026-10-28
- 相关文件：迁移 `0012_notifications`、`app/models/notifications.py`、`app/services/notification_service.py`、`app/workers/consumers.py`、`app/workers/main.py`
- 相关需求：M1 第 8.6 章（主动动作可靠性）、S3（可观测性）

## 背景

主动建议"已推送"是一个对外可见的动作，必须具备：与建议同事务登记、重复投递幂等、失败可重试、
以及"到底有没有送出去"的明确状态。直接在扫描事务里调用 APNs 会让外部网络故障拖垮扫描，
也无法解释"建议在列表里但没收到推送"。

## 决策

1. **两阶段**：扫描事务内通过 Outbox 发 `proactive.suggestion.proposed` 事件；消费者创建
   `notifications` 行（`(org, suggestion_id, provider)` 唯一）。
2. **Worker 投递**：`dispatch_pending` 按 provider 发送；失败累计 `attempts`，超过上限转
   `FAILED` 并保留 `last_error`；未超限保持 `PENDING` 等下一轮。
3. **provider 诚实边界**：本地默认 `inapp`（通知列表即通道，落库即送达）；`log` 只写日志；
   `apns` 未配置证书与设备 token 时抛 `PushUnavailable`，**不假装发送成功**。
4. **抑制/保留的建议不通知**：`push_status != PUSHED` 时消费者不创建通知。
5. **可查询与已读**：`GET /v1/notifications` 返回当前用户通知与未读数；`:read` 幂等标记已读。

## 后果

- 好处：推送失败不阻断主动式扫描；重投幂等；状态可解释。
- 代价：真实 APNs/邮件仍需要凭证、设备 token 注册与退订管理；当前只把 provider 接缝留好。
