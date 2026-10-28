# ADR-0009：PII 字段加密与留存脱敏

- 状态：已接受（Accepted）
- 日期：2026-10-28
- 相关文件：`app/models/encrypted.py`、`app/services/retention_service.py`、`app/api/routes/admin.py`、`app/workers/main.py`、`docs/architecture/pii.md`
- 相关需求：S4（PII 与合规）、设计 §11.4/§9.3

## 背景

联系人姓名/邮箱/电话与现场笔记是高敏感商业信息；数据库备份泄露即可直接读到。设计还要求
"被遗忘权"与留存期限，而此前的实现只有 RLS 与记忆摘要删除。

## 决策

1. **字段级加密**：客户联系人三字段与 `visit_records.notes` 使用 `EncryptedText`（Fernet）。
   密钥来自 `GBT_FIELD_ENCRYPTION_KEY`；未设置时明文（本地开发），生产必须设置。
   读取时解密失败原样返回，保证开启加密前的明文行可平滑迁移。
2. **留存脱敏**：`retention_service.apply_retention` 把超过 `GBT_RETENTION_DAYS` 的现场笔记
   清空、照片引用清空、记忆正文/决策/待办/教训/向量清空并置 `valid_until`；保留行壳与时间戳。
   Worker 每日执行，`POST /v1/admin:retention` 可手动触发。
3. **不伪装全盘加密**：文档明确这是应用层字段加密，密钥泄露等于明文泄露；生产需 KMS 托管
   与轮换。
4. **不做不可逆哈希**：联系人字段需要展示/搜索，必须可解密；哈希只适合不需要还原的标识。

## 后果

- 好处：备份泄露时联系人/笔记不可直接读；旧数据兼容；留存可自动执行。
- 代价：加密字段无法用 SQL 直接 LIKE/排序（当前也没有这类查询）；轮换需要回填脚本。
