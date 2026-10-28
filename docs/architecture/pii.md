# PII 数据字典与合规处理

对应设计文档 v2.0 第 11.4 章（PII 与合规）与第 9.3 章（记忆失效/遗忘）。
本文件描述**当前实现**与**仍缺项**，不把"计划要做"写成"已经做了"。

## 1. 数据分级

| 字段/数据 | 所在位置 | 分级 | 当前处理 |
|---|---|---|---|
| 姓名、称呼、客户联系人姓名 | `customers.primary_contact_name`、`users.display_name` | SENSITIVE | 租户隔离（RLS）；无字段级加密 |
| 邮箱、电话 | `customers.primary_contact_email/phone` | SENSITIVE | 租户隔离；API 原样返回（本地开发） |
| 护照号、证件有效期 | 尚未建模（签证清单为草稿文本） | RESTRICTED | 不落库；主动建议只生成"需核验"提醒 |
| 拜访笔记、会议纪要草稿 | `visit_records.notes`、`llm_invocations.output_json` | SENSITIVE | 租户隔离；纪要仅草稿，不对外发送 |
| 坐标/地址 | `places.lat/lon/address_line` | SENSITIVE | 租户隔离；用于行程计算 |
| 照片引用 | `visit_records.photos` | RESTRICTED | 只存引用字符串，不存二进制 |
| 行程摘要/教训/待办 | `trip_memory_summaries` | SENSITIVE | 租户隔离；支持合规删除（清空正文与向量） |
| 护照有效期 | `visa_cases.passport_expires_at` | RESTRICTED | 只存日期，不存护照号；用于出发前时效检查 |
| 费用/凭证引用 | `trip_expenses` | SENSITIVE | 金额与备注；凭证只存引用，不存票据二进制 |
| 审批人/操作人 | `approvals`、`suggestion_audit`、`llm_invocations` | AUDIT | 保留用于审计，不参与业务展示 |

## 2. 已实现的控制

1. **数据库级租户隔离**：所有组织级表 `ENABLE` + `FORCE ROW LEVEL SECURITY`；应用角色非表 owner；
   每个事务 `SET LOCAL app.current_org`（ADR-0002、迁移 `0010_security_rls`）。
2. **访问审计**：建议生命周期（`suggestion_audit`）、审批（`approvals`）、LLM 调用
   （`llm_invocations`）、Outbox 事件与按 Trip 的审计时间线（`GET /v1/trips/{id}/audit`）。
3. **合规删除**：`DELETE /v1/memory/{summary_id}`（ADMIN）把摘要正文/决策/待办/教训/向量清空，
   并置 `valid_until`；不是"只隐藏不删除"。
4. **最小化落库**：签证目录只生成清单草稿，不保存护照号；会议纪要只保存草稿与调用记录。
5. **请求日志不落敏感 body**：结构化日志只输出白名单字段（trace/org/trip/usecase/latency/status），
   不记录请求体或响应体。
6. **字段级加密**：设置 `GBT_FIELD_ENCRYPTION_KEY`（Fernet key）后，客户联系人姓名/邮箱/电话与
   现场笔记以密文落库；旧明文行在读取时原样兼容，新写入一律加密。
7. **留存脱敏**：Worker 按 `GBT_RETENTION_DAYS` 定期清空过期现场笔记与记忆正文（`POST /v1/admin:retention`
   可手动触发），保留审计壳与脱敏时间。
8. **密钥轮换**：`scripts/rotate_field_key.py` 用旧 key 解密、新 key 重加密；明文默认跳过，
   `--include-plaintext` 才会一并加密，避免误把密文当明文二次加密。

## 3. 仍缺项（生产前必须补）

| 缺口 | 影响 | 建议 |
|---|---|---|
| 密钥托管 | 当前 key 来自环境变量 | 接入 KMS/密钥管理；轮换脚本已提供 |
| 被遗忘权覆盖客户/记录 | 目前只支持删除记忆摘要与留存脱敏 | 增加按客户/拜访的删除或匿名化流程（保留审计壳） |
| 生产身份认证 | 本地用 `X-Org-Id/X-User-Id` 头 | 网关/OIDC 断言注入，关闭 `GBT_DEV_AUTH` |
| 导出/下载审计 | 无导出接口 | 如新增导出，必须记录操作人与范围 |

> 本文件随实现更新；任何新增 PII 字段必须同时更新本表与 `docs/decisions/`。
