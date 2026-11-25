# Docker 完整栈运行手册

## 启动

```bash
make stack-up      # DB + API + Worker，构建 gbt-os-api:local
curl http://127.0.0.1:8080/v1/health
```

`api` 容器启动时先执行 `alembic upgrade head`，再启动 uvicorn；`worker` 等 API 健康后启动。
数据库仍是 compose 里的 `db` 服务（容器名 `zhi-tu-pg`，宿主端口 55432），数据卷 `gbt_os_pg_data`。

## 常用命令

```bash
docker compose -f infra/docker/docker-compose.yml --profile app ps
docker compose -f infra/docker/docker-compose.yml --profile app logs -f api worker
make stack-down
```

只起数据库（开发/测试常用）：

```bash
make db-up
```

## 配置

API/Worker 共用 `x-app-env` 锚点，关键变量：

| 变量 | 说明 |
|---|---|
| `GBT_DATABASE_URL` | 应用连接（`gbt_app`，受 RLS） |
| `GBT_MIGRATION_DATABASE_URL` | 迁移连接（`gbt_owner`） |
| `GBT_DEV_AUTH` | 本地身份头开关；生产应关闭并接 OIDC 网关 |
| `GBT_PUSH_PROVIDER` | `inapp` / `log` / `smtp` / `apns` |
| `GBT_FIELD_ENCRYPTION_KEY` | Fernet key；不设置则 PII 明文（仅本地） |

生产凭证不要写进 compose 文件：用 `docker compose --env-file` 或编排平台的 secret 注入。

### SMTP

```bash
GBT_PUSH_PROVIDER=smtp \
GBT_SMTP_HOST=smtp.example.com GBT_SMTP_PORT=587 GBT_SMTP_USE_TLS=true \
GBT_SMTP_USERNAME=... GBT_SMTP_PASSWORD=... GBT_SMTP_FROM=no-reply@example.com \
make stack-up
```

### APNs

需要 token-based `.p8`、Key ID、Team ID、bundle id：

```bash
GBT_PUSH_PROVIDER=apns \
GBT_APNS_KEY_ID=... GBT_APNS_TEAM_ID=... GBT_APNS_TOPIC=com.example.app \
GBT_APNS_PRIVATE_KEY=/run/secrets/AuthKey_XXX.p8 \
GBT_APNS_USE_SANDBOX=true \
make stack-up
```

客户端先调用 `POST /v1/devices` 注册设备 token；通知入队时会快照最近活跃的 token/邮箱。
未配置或缺少 token 时通知进入重试/失败并保留 `last_error`，不会伪造送达。

## APNs 推送（收口说明）

代码链路完整（ES256 JWT + HTTP/2，token-based .p8），上线只剩三步操作：

1. App Store Connect → Users and Access → Integrations → Keys：生成 APNs key（.p8），
   记下 Key ID；
2. `GBT_PUSH_PROVIDER=apns GBT_APNS_KEY_ID=... GBT_APNS_TEAM_ID=XC9LAN9MW9
   GBT_APNS_TOPIC=com.anari.globaltravelos GBT_APNS_PRIVATE_KEY=/run/secrets/AuthKey.p8`；
3. 客户端 `POST /v1/devices` 注册 token 后即真实推送。

本地无 Apple 凭证时两条路：保持 `inapp`（通知中心即通道），或
`GBT_APNS_API_BASE` 指向本地 APNs mock 端到端联调（JWT/请求结构不变，只换目标地址）。
**410 Unregistered 语义**：Apple 回 410 时 dispatch 自动禁用该设备 token 并把通知
直接转 FAILED（失效不会自愈，不烧重试次数）。

## SMTP 邮件通道（MailPit，开源）

栈里自带 **MailPit**（开源 SMTP 收件池，ADR-0034）：`make stack-up` 后即运行，
网页界面 http://localhost:8025 可以直接看发出的邮件。切换邮件通道：

```bash
# 在 x-app-env（compose）或 .env 里：
GBT_PUSH_PROVIDER=smtp
GBT_SMTP_HOST=mailpit      # 容器网络内用服务名；宿主机跑 API 时用 localhost
GBT_SMTP_PORT=1025
GBT_SMTP_FROM=dev@example.local
```

生产换真实 SMTP（Brevo/Mailgun 免费档或自有服务器）只需改 host/port/凭证；
未配置或发送失败保留 `last_error`，不伪造送达。APNs 无开源替代（Apple 专有），
未配置时 app 内通知中心（inapp）即为兜底通道。

## 免钥公开数据源（ADR-0034）

| 源 | 开关 | 用途 |
|---|---|---|
| Open-Meteo | `GBT_WEATHER_PROVIDER=open-meteo` | 天气信号拉取 + 指南实时天气（免 key） |
| OpenSky | `GBT_FLIGHT_PROVIDER=opensky` | FLIGHT_STATUS 信号（匿名低速率，可免费注册） |
| OSRM | `GBT_TRAVEL_PROVIDER=osrm` | GROUND 段真实驾车时间（demo 限速，生产自托管） |
| Frankfurter | `GBT_FX_PROVIDER=frankfurter` | FX 行情同步（见 ADR-0033） |

拉取入口（ADMIN）：`POST /v1/integrations/signals:pull-weather`（body: trip_id）、
`signals:pull-flight`（body: trip_id + callsign）。源未配置时端点 503 显式失败。

## 迁移与回滚

- 迁移只由 API 容器启动时执行；多副本部署时应改为独立的 one-shot 迁移 Job，避免并发升级。
- 迁移不可变：改动 = 新迁移；回滚用新迁移或备份恢复，不直接 downgrade 生产库。
- 字段密钥轮换：`scripts/rotate_field_key.py`，完成后更新 `GBT_FIELD_ENCRYPTION_KEY` 并重启。

## 健康与排障

```bash
docker compose ... exec api python -m alembic -c alembic.ini current
docker compose ... exec db psql -U gbt_app -d gbt_os -c "select 1"
docker compose ... logs --tail 100 api
```

结构化日志（`app.http`）包含 `trace_id/org_id/trip_id/latency_ms/status_code`，可直接与响应头
`X-Trace-Id` 对照。

## 私有票据对象存储与孤儿对账（H2.13 / H2.14）

票据原件存储在 PostgreSQL 之外。local 环境可用私有 filesystem；Docker 开发栈将数据保存在 `gbt_os_receipt_data` 持久卷。预发/生产必须配置私有 S3 兼容 Bucket，禁止 public ACL。

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `GBT_RECEIPT_STORAGE_PROVIDER` | `filesystem` | 生产设置为 `s3` |
| `GBT_RECEIPT_S3_BUCKET` / `GBT_RECEIPT_S3_REGION` | — / `us-east-1` | 私有 Bucket 与区域 |
| `GBT_RECEIPT_S3_ENDPOINT_URL` | — | S3 兼容服务可选 endpoint |
| `GBT_RECEIPT_RECONCILIATION_INTERVAL_SECONDS` | `3600` | 每租户对账间隔 |
| `GBT_RECEIPT_ORPHAN_GRACE_SECONDS` | `86400` | 对象 LastModified 和首次孤儿观察时间都必须超过该宽限期 |
| `GBT_RECEIPT_ORPHAN_CLEANUP_ENABLED` | `false` | false 只发现、审计和报告；true 才允许删除合格孤儿 |

S3 运行身份按最小权限配置：`s3:ListBucket` 只允许 `receipts/{organization_id}/` 前缀；`s3:GetObject`、`s3:PutObject`、`s3:DeleteObject` 只允许 Bucket 下 `receipts/*` 对象。对账需要 `ListBucket` 和 `HeadObject`（通常由 `s3:GetObject` 授权）；403/AccessDenied 会记录错误，不能被当成对象不存在。上传时使用服务端 SHA-256 与 S3 `ServerSideEncryption=AES256`。

**安全启用顺序：**

1. 先在预发保持 `GBT_RECEIPT_ORPHAN_CLEANUP_ENABLED=false`，确认 Worker 每小时输出扫描数、首次发现数和“满足清理条件”数。
2. 核对 `DISCOVERED` 记录、存储 Bucket 生命周期策略、备份/保留要求与租户前缀 IAM；确认没有合法历史对象被误判。
3. 只有运维审批后，才把 cleanup 开关设为 true 并重启 Worker。开关不是 API 用户可控项。
4. 观察 `DELETE_FAILED`、`MISSING` 和 `RESOLVED` 状态；`DELETE_FAILED` 会在后续周期重试，`MISSING` 表示对象缺失且系统没有证据证明是本对账器删除的。

上传与对账共用 organization 级 PostgreSQL transaction advisory lock；对账列举完存储后取得锁，再读取数据库并对列表未包含的已登记 key 执行单对象 HEAD/stat，避免把并发完成的新上传误报为缺失。删除前会二次核验 `ReceiptAsset`；任何仍有元数据的资产都不会被当作孤儿清理。`receipt_storage_orphans` 保留对象 key、首次观察、删除尝试、状态和错误；不保存文件内容。
