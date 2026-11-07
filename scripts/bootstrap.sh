#!/usr/bin/env bash
# 一键把本地环境拉起来：venv → 依赖 → 数据库 → 迁移 → 种子数据。
# 用法：bash scripts/bootstrap.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PATH="/usr/local/bin:$PATH"   # OrbStack

# 项目按 Python 3.13 校准（pyproject target-version / mypy python_version / CI）。
# 系统自带 python3 可能过老（如 macOS 的 3.9），按新→旧挑选可用解释器。
if [ ! -x .venv/bin/python ]; then
  PYBIN=""
  for cand in python3.13 python3.12 python3.11 python3; do
    if command -v "$cand" >/dev/null 2>&1; then PYBIN="$cand"; break; fi
  done
  if [ -z "$PYBIN" ]; then
    echo "找不到可用的 python3（需要 3.11+，推荐 3.13）" >&2
    exit 1
  fi
  echo "[1/5] 创建虚拟环境（$($PYBIN --version)）"
  "$PYBIN" -m venv .venv
else
  echo "[1/5] 虚拟环境已存在"
fi

echo "[2/5] 安装依赖"
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements-dev.txt

if [ ! -f .env ]; then
  echo "[3/5] 从 .env.example 生成 .env"
  cp .env.example .env
else
  echo "[3/5] .env 已存在"
fi

echo "[4/5] 启动数据库并迁移"
docker compose -f infra/docker/docker-compose.yml up -d --build
until docker exec zhi-tu-pg pg_isready -U postgres -d gbt_os >/dev/null 2>&1; do sleep 1; done
.venv/bin/python -m alembic -c services/api/alembic.ini upgrade head

# 种子数据是集成测试与 e2e 的前置（身份/组织/出行/拜访）；幂等，可重复执行。
# --no-fixture：Excel 夹具已提交进仓库，重生成只会弄脏工作区。
echo "[5/5] 造种子数据（幂等）"
.venv/bin/python scripts/seed_dev.py --no-fixture

echo "完成。启动 API： make api ；启动 Worker： make worker ；跑测试： make test"
