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
