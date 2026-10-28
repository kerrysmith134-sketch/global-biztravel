#!/usr/bin/env bash
# 一键把本地环境拉起来：venv → 依赖 → 数据库 → 迁移。
# 用法：bash scripts/bootstrap.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="/usr/local/bin:$PATH"   # OrbStack

if [ ! -x .venv/bin/python ]; then
  echo "[1/4] 创建虚拟环境"
  python3 -m venv .venv
else
  echo "[1/4] 虚拟环境已存在"
fi

echo "[2/4] 安装依赖"
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements-dev.txt

if [ ! -f .env ]; then
  echo "[3/4] 从 .env.example 生成 .env"
  cp .env.example .env
else
  echo "[3/4] .env 已存在"
fi

echo "[4/4] 启动数据库并迁移"
docker compose -f infra/docker/docker-compose.yml up -d --build
until docker exec zhi-tu-pg pg_isready -U postgres -d gbt_os >/dev/null 2>&1; do sleep 1; done
.venv/bin/python -m alembic -c services/api/alembic.ini upgrade head

echo "完成。启动 API： make api ；启动 Worker： make worker"