# 云服务器部署（ADR-0035）——"手机随时连，Mac 随便关"

目标架构：一台 VPS 跑整套 docker compose 栈（PostgreSQL + API + Worker），
公网暴露 8080（http + 部署 token `X-API-Key`），TestFlight 包默认直连。

## 前置

- 一台 VPS（推荐 Oracle Cloud 免费层 ARM 4C/24G；或任何 ¥30-60/月 轻量服务器，
  Ubuntu 22.04+ / Debian 12）
- 安全组/防火墙放行 TCP 8080（云控制台操作；IP 直连不涉域名备案）
- 本仓库（VPS 上 `git clone`，或 `rsync` 整个目录过去）

## 一键部署（VPS 上执行）

```bash
cd <仓库根目录>
bash infra/deploy/bootstrap.sh
```

脚本做五件事：装 Docker（如缺）→ 构建镜像 → 起栈（自动迁移到 head）→
等健康 → 冒烟（trips 200 + token 401 校验）。

## 验证

```bash
curl http://<VPS_IP>:8080/v1/health                       # 200（免 token）
curl -H "X-API-Key: gbt-os-demo-2026" \
     -H "X-Org-Id: 11111111-1111-1111-1111-111111111111" \
     -H "X-User-Id: 22222222-2222-2222-2222-222222222222" \
     http://<VPS_IP>:8080/v1/trips?limit=3                # 200
curl http://<VPS_IP>:8080/v1/trips                        # 401（无 token）
```

## iOS 侧切换

`apps/ios/GlobalTravelOS/Core/Networking/APIClient.swift`：

```swift
static let defaultBaseURLString = "http://<VPS_IP>:8080"
```

然后 `BUILD_NUMBER=<n> bash scripts/ios_release.sh --upload` 重新发包。

## 数据迁移（可选，把 Mac 上的演示数据搬上云）

```bash
# Mac 上导出
docker exec zhi-tu-pg pg_dump -U postgres -d gbt_os > /tmp/gbt_os.sql
# 传到 VPS 后导入
scp /tmp/gbt_os.sql vps:/tmp/
ssh vps "docker exec -i zhi-tu-pg psql -U postgres -d gbt_os < /tmp/gbt_os.sql"
```

不做迁移则是干净库：部署完在 VPS 上跑
`docker exec gbt-os-api-1 python /app/scripts/seed_dev.py && docker exec gbt-os-api-1 python /app/scripts/seed_demo.py --api http://localhost:8080`
重建种子与演示数据（注意镜像内 scripts 在 /app/scripts）。

## 安全边界（测试期取舍，写清楚不糊弄）

- `GBT_DEV_AUTH=true`：身份由请求头声明，任何人知道 org/user id 即可冒充——
  **测试期可接受**（数据是演示数据）；生产必须接真身份（Apple 登录 + 会话）。
- 部署 token 是共享对称密钥，防扫描器噪音，不是认证。
- API 绑定 `127.0.0.1`：8080 只对本机开。若不用反代，bootstrap 会把 ports 改成
  `0.0.0.0:8080`（脚本里显式开关，默认开公网 + token）。
- 升级强安全（https 域名、真认证、速率限制）在生产化阶段处理。

## 常用运维

```bash
docker compose -f infra/deploy/docker-compose.prod.yml logs -f api     # 日志
docker compose -f infra/deploy/docker-compose.prod.yml down            # 停（保数据卷）
docker compose -f infra/deploy/docker-compose.prod.yml up -d --build   # 更新代码重发
```
