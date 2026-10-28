SHELL := /bin/zsh
# 绝对路径：migrate/revision/worker 需要在子目录里执行，相对路径会失效。
# 必须加引号：工作目录含空格（".../Coding Work/..."），不加会被 shell 拆成两段。
PY := "$(CURDIR)/.venv/bin/python"
PIP := $(PY) -m pip
COMPOSE := docker compose -f infra/docker/docker-compose.yml
# OrbStack 的 docker CLI 不在默认 PATH 中
export PATH := /usr/local/bin:$(PATH)

.PHONY: help venv install db-up db-down db-reset psql migrate revision check api worker \
        outbox proactive test test-unit test-integration typecheck lint openapi contract-check bench e2e clean ios-build ios-test

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

venv: ## 创建虚拟环境
	python3 -m venv .venv

install: ## 安装依赖
	$(PIP) install -q --upgrade pip
	$(PIP) install -q -r requirements-dev.txt

db-up: ## 构建镜像并启动 PostgreSQL16+PostGIS+pgvector
	$(COMPOSE) up -d --build
	@until docker exec zhi-tu-pg pg_isready -U postgres -d gbt_os >/dev/null 2>&1; do sleep 1; done
	@echo "db ready on localhost:55432"

db-down: ## 停止数据库（保留数据卷）
	$(COMPOSE) down

db-reset: ## 删除数据卷并重建（干净库）
	$(COMPOSE) down -v
	$(MAKE) db-up

psql: ## 以迁移角色进入 psql
	docker exec -it zhi-tu-pg psql -U gbt_owner -d gbt_os

migrate: ## 执行 Alembic 迁移
	cd services/api && $(PY) -m alembic -c alembic.ini upgrade head

revision: ## 生成新迁移：make revision m="message"
	cd services/api && $(PY) -m alembic -c alembic.ini revision --autogenerate -m "$(m)"

check: ## 校验模型与迁移是否漂移
	cd services/api && $(PY) -m alembic -c alembic.ini check

api: ## 启动 API（开发模式）
	$(PY) -m uvicorn app.main:app --app-dir services/api --reload --port 8080

worker: ## 启动后台 Worker（Outbox / 规划作业 / 主动式调度）
	cd services/api && $(PY) -m app.workers.main $(ARGS)

openapi: ## 从 FastAPI 导出 contracts/openapi.yaml
	$(PY) scripts/export_openapi.py

contract-check: ## 校验代码与 contracts/openapi.json 是否漂移（S6）
	$(PY) scripts/check_contract.py

bench: ## 规划引擎基准：OR-Tools / 启发式 p50/p95（S2/N3）
	$(PY) scripts/bench_planning.py

e2e: ## 真实后端全链路验收（需 make api 运行）
	$(PY) scripts/acceptance_e2e.py

test: ## 全量测试
	$(PY) -m pytest

test-unit: ## 仅单元测试（不需要数据库）
	$(PY) -m pytest -m "not integration"

test-integration: ## 集成测试（需要 db-up）
	$(PY) -m pytest -m integration

lint: ## ruff 检查
	$(PY) -m ruff check services/api packages scripts

clean: ## 清理缓存
	find . -name __pycache__ -type d -not -path "./.venv/*" -prune -exec rm -rf {} + ; rm -rf .pytest_cache .ruff_cache

ios-build: ## 构建 iOS 客户端（模拟器）
	cd apps/ios && xcodebuild -project GlobalTravelOS.xcodeproj -scheme GlobalTravelOS \
		-destination 'platform=iOS Simulator,name=iPhone Duo' build

ios-test: ## 运行 iOS 单元测试
	cd apps/ios && xcodebuild -project GlobalTravelOS.xcodeproj -scheme GlobalTravelOS \
		-destination 'platform=iOS Simulator,name=iPhone Duo' test