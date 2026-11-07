SHELL := /bin/zsh
# 绝对路径：migrate/revision/worker 需要在子目录里执行，相对路径会失效。
# 必须加引号：工作目录含空格（".../Coding Work/..."），不加会被 shell 拆成两段。
PY := "$(CURDIR)/.venv/bin/python"
PIP := $(PY) -m pip
COMPOSE := docker compose -f infra/docker/docker-compose.yml
# OrbStack 的 docker CLI 不在默认 PATH 中
export PATH := /usr/local/bin:$(PATH)
# travel_planner（packages/planning-engine）刻意不打包安装：Docker 走 ENV PYTHONPATH、
# pytest 走 pyproject 的 pythonpath；本地 make 进程（worker / api / e2e 的 Worker 子进程）
# 必须在这里补齐，否则 `import travel_planner` 直接 ModuleNotFoundError。
export PYTHONPATH := $(CURDIR)/services/api:$(CURDIR)/packages/planning-engine$(if $(PYTHONPATH),:$(PYTHONPATH))

.PHONY: help venv install db-up db-down db-reset psql migrate revision check api worker \
        outbox proactive seed test test-unit test-integration typecheck lint openapi contract-check bench e2e stack-up stack-down ci ci-backend lint-ios clean ios-build ios-test

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

e2e: seed ## 真实后端全链路验收（需 make api 运行；自动先补齐种子数据）
	$(PY) scripts/acceptance_e2e.py

stack-up: ## 启动完整 Docker 栈（DB + API + Worker）
	$(COMPOSE) --profile app up -d --build

stack-down: ## 停止完整 Docker 栈
	$(COMPOSE) --profile app down

ci: ## 完整门禁：静态检查 + 契约 + 后端测试 + Docker E2E + iOS
	bash scripts/ci.sh --with-ios

ci-backend: ## 后端门禁：静态检查 + 契约 + 后端测试
	bash scripts/ci.sh

lint-ios: ## SwiftLint（未安装则提示并跳过，不伪造通过）
	@command -v swiftlint >/dev/null 2>&1 && swiftlint --strict || echo "swiftlint 未安装，跳过（CI 镜像需安装 SwiftLint）"

seed: ## 造种子数据（幂等；--no-fixture 不改已提交的 Excel 夹具）
	$(PY) scripts/seed_dev.py --no-fixture

test: seed ## 全量测试（真实 PostgreSQL；自动先补齐种子数据）
	$(PY) -m pytest

test-unit: ## 仅单元测试（不需要数据库）
	$(PY) -m pytest -m "not integration"

test-integration: seed ## 集成测试（需要 db-up；自动先补齐种子数据）
	$(PY) -m pytest -m integration

typecheck: ## MyPy 类型检查（API + 规划引擎）
	$(PY) -m mypy services/api/app packages/planning-engine/travel_planner

lint: ## ruff 检查
	$(PY) -m ruff check services/api packages scripts

clean: ## 清理缓存
	find . -name __pycache__ -type d -not -path "./.venv/*" -prune -exec rm -rf {} + ; rm -rf .pytest_cache .ruff_cache

# 默认目标是原开发机上的自定义模拟器；新机器上通常不存在，ios-test 会自动回退到
# 第一台可用 iPhone 模拟器（CI 覆盖 IOS_DEST 或本机存在同名设备时行为不变）。
IOS_DEST ?= platform=iOS Simulator,name=iPhone Duo

ios-build: ## 构建 iOS 客户端（通用模拟器目标，不依赖具体设备）
	cd apps/ios && xcodebuild -project GlobalTravelOS.xcodeproj -scheme GlobalTravelOS \
		-destination 'generic/platform=iOS Simulator' build

ios-test: ## 运行 iOS 单元测试 + UI 冒烟（需 make api 运行；模拟器名用 IOS_DEST 覆盖）
	@name="$(IOS_DEST)"; name="$${name##*name=}"; \
	if ! xcrun simctl list devices available | grep -q "$$name"; then \
	  fallback=$$(xcrun simctl list devices available | sed -n 's/^ *\(iPhone [^(]*\) (.*/\1/p' | head -1 | sed 's/ *$$//'); \
	  if [ -z "$$fallback" ]; then echo "没有可用的 iPhone 模拟器"; exit 1; fi; \
	  echo "目标模拟器 '$$name' 不存在，回退到 '$$fallback'"; \
	  dest="platform=iOS Simulator,name=$$fallback"; \
	else \
	  dest='$(IOS_DEST)'; \
	fi; \
	cd apps/ios && xcodebuild -project GlobalTravelOS.xcodeproj -scheme GlobalTravelOS \
		-destination "$$dest" test