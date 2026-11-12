#!/usr/bin/env bash
# 云上一键部署（ADR-0035）：Docker 安装 → 镜像构建 → 起栈 → 迁移 → 冒烟。
# 前提：Ubuntu 22.04+/Debian 12，仓库已 clone，安全组已放行 TCP 8080。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "== 1. Docker（如缺）=="
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
fi
docker --version

echo "== 2. 构建镜像 + 起栈 =="
docker compose -f infra/deploy/docker-compose.prod.yml up -d --build

echo "== 3. 等健康 =="
for i in $(seq 1 30); do
  if docker compose -f infra/deploy/docker-compose.prod.yml ps api | grep -q healthy; then
    break
  fi
  sleep 5
done
docker compose -f infra/deploy/docker-compose.prod.yml ps

echo "== 4. 冒烟 =="
sleep 3
HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8080/v1/health)
echo "health（免 token）: $HEALTH"
NO_TOKEN=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "X-Org-Id: 11111111-1111-1111-1111-111111111111" \
  -H "X-User-Id: 22222222-2222-2222-2222-222222222222" \
  "http://127.0.0.1:8080/v1/trips?limit=1")
echo "无 token 期望 401: $NO_TOKEN"
WITH_TOKEN=$(curl -s -o /dev/null -w "%{http_code}" \
  -H "X-API-Key: gbt-os-demo-2026" \
  -H "X-Org-Id: 11111111-1111-1111-1111-111111111111" \
  -H "X-User-Id: 22222222-2222-2222-2222-222222222222" \
  "http://127.0.0.1:8080/v1/trips?limit=1")
echo "带 token 期望 200: $WITH_TOKEN"

if [[ "$HEALTH" == "200" && "$NO_TOKEN" == "401" && "$WITH_TOKEN" == "200" ]]; then
  echo "== 部署成功 =="
  echo "iOS 默认地址改为：http://<本机公网IP>:8080（改 APIClient.swift 后重发包）"
  echo "种子/演示数据（可选）："
  echo "  docker exec gbt-os-api-1 python /app/scripts/seed_dev.py"
  echo "  docker exec gbt-os-api-1 python /app/scripts/seed_demo.py --api http://localhost:8080"
else
  echo "== 冒烟未通过，看日志：docker compose -f infra/deploy/docker-compose.prod.yml logs api =="
  exit 1
fi
