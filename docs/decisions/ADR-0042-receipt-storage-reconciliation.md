# ADR-0042：私有票据对象采用租户级双宽限期孤儿对账

- 状态：已接受
- 日期：2026-11-25

## 背景

ReceiptAsset 采用“先写私有对象、再写 PostgreSQL 元数据”的跨资源流程。数据库事务回滚、进程崩溃或对象存储删除失败，都可能留下 PostgreSQL 中没有对应 ReceiptAsset 的对象。反方向也可能出现 ReceiptAsset 仍存在、对象却已丢失的情况。

对象存储与 PostgreSQL 没有共同事务，因此不能声称两者天然原子一致；也不能在一次 List + DB 查询后立刻删除未知对象，否则可能与一个正在完成的上传竞态。

## 决策

1. **统一列举接口**：ReceiptStorage 增加租户前缀限定的 \`list_objects(prefix)\`，返回 key、size 和 last-modified 元数据，不读取票据字节、不生成公开或预签名 URL。filesystem adapter 只遍历指定组织目录；S3 adapter 使用 \`ListObjectsV2\` paginator。
2. **租户隔离**：调用方必须提供 \`receipts/{organization_id}/\` 前缀。服务端再次验证每个返回 key 的组织 ID 与 UUID 结构，并使用当前 RLS 绑定组织读取 ReceiptAsset。
3. **双宽限期**：孤儿对象必须同时满足 \`object.last_modified <= now - grace\` 和 \`first_seen_at <= now - grace\`，默认 grace 为 24 小时。第一次观察只写审计，不删除。即使对象本身很旧，也至少要经过一个完整观察窗口。
4. **默认 dry-run**：\`GBT_RECEIPT_ORPHAN_CLEANUP_ENABLED\` 默认为 false。Worker 可以发现、审计、统计 eligible 对象，但不会删除。只有运维显式开启后，才允许删除满足双宽限期条件的对象。
5. **上传/对账互斥**：ReceiptAsset 上传在 PUT 前取得以 organization UUID 派生的 PostgreSQL transaction advisory lock，直到 API 事务提交；对账在整个列举、数据库核对与对象删除事务期间取得同一租户锁，防止对象已 PUT、元数据尚未提交时被误删。
6. **二次核验**：删除前再次查询同一 organization/provider/key 的 ReceiptAsset。无论该资产处于 AVAILABLE、QUARANTINED 还是 DELETE_PENDING，只要元数据行存在就不得删除对象。
7. **持久化审计状态**：\`receipt_storage_orphans\` 记录 DISCOVERED、DELETE_PENDING、DELETE_FAILED、DELETED、MISSING、RESOLVED。删除失败保留原因和尝试次数，后续周期可重试；对象自然消失但没有可证明的清理成功时记录 MISSING，而不伪称由系统删除。
8. **按租户调度**：Worker 每个租户默认每小时执行一次对账。S3 运行身份必须具有 \`ListBucket\`（受限到 \`receipts/{organization_id}/\` 前缀）、Get/Put/Delete object 所需权限；若未配置列举权限，对账会显式失败并写 Worker 日志，不影响 API 上传/下载。
9. **不替代证据完整性校验**：ReceiptAsset 下载仍验证 SHA-256。对账发现元数据存在而对象缺失时标记 MISSING，下载路径仍拒绝返回缺失/损坏内容；对账不会修复、重建或伪造原件。

## 后果

- 未知对象不再只能靠人工猜测；首次发现、观察期限、删除尝试与结果均有组织级审计记录。
- 默认部署安全地只做发现与预览；启用清理前，运维可先观察 eligible 数量与日志。
- 对象删除是幂等的；跨资源提交异常可能令审计状态短暂落后，但下一次扫描会以对象存储和数据库现状重新归类，不会把已登记资产当作孤儿。
- 生产环境应先验证 S3 ListBucket 前缀权限与持久化 Bucket 配置，再单独启用清理；该开关不由应用自行打开。
