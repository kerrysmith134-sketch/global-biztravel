#!/usr/bin/env bash
# 本地/CI 统一门禁（PRD MVP-1 §8）。默认跑后端；传 --with-ios 追加 iOS 构建与测试。
#
# 前置：Docker（自建 PostgreSQL 镜像）。
# 用法：bash scripts/ci.sh [--with-ios]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
WITH_IOS=0
[[ "${1:-}" == "--with-ios" ]] && WITH_IOS=1

echo "== 静态检查 =="
make lint
make typecheck
make check

echo "== 契约漂移 =="
make contract-check

echo "== 文档漂移 =="
make docs-check

echo "== 数据库 =="
make db-up
make migrate

echo "== 后端测试（真实 PostgreSQL） =="
make test

if [[ "$WITH_IOS" == "1" ]]; then
  echo "== 完整栈 + 真实 E2E =="
  make stack-up
  cleanup() { docker compose -f infra/docker/docker-compose.yml --profile app stop api worker >/dev/null 2>&1 || true; }
  trap cleanup EXIT
  until curl -fsS http://127.0.0.1:8080/v1/health >/dev/null 2>&1; do sleep 2; done
  make e2e

  echo "== iOS 构建与测试 =="
  make lint-ios
  make ios-build
  make ios-test
fi

echo "CI 通过。"
