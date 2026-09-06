.PHONY: install dev infra-up infra-down migrate check test-db-up test-db-down test-integration

install:
	pnpm install
	python3 -m venv .venv-api
	python3 -m venv .venv-worker
	.venv-api/bin/pip install -e 'services/api[dev]'
	.venv-worker/bin/pip install -e 'services/worker[dev]'

dev:
	pnpm dev

infra-up:
	docker compose up -d postgres redis

infra-down:
	docker compose down

migrate:
	for f in infra/migrations/*.sql; do psql "$${DATABASE_URL}" -v ON_ERROR_STOP=1 -f "$$f" || exit 1; done

TEST_DATABASE_URL?=postgresql+asyncpg://seo_autopilot:seo_autopilot@127.0.0.1:5433/seo_autopilot_test

test-db-up:
	docker compose --profile test up -d --wait postgres-test

test-db-down:
	docker compose --profile test rm -sf postgres-test

test-integration: test-db-up
	TEST_DATABASE_URL="$(TEST_DATABASE_URL)" PYTHONPATH=services/api .venv-api/bin/pytest services/api/tests/integration

check: test-db-up
	pnpm check
	.venv-api/bin/ruff check services/api
	.venv-api/bin/pyright --pythonpath .venv-api/bin/python services/api
	.venv-worker/bin/ruff check services/worker
	.venv-worker/bin/pyright --pythonpath .venv-worker/bin/python services/worker
	TEST_DATABASE_URL="$(TEST_DATABASE_URL)" PYTHONPATH=services/api .venv-api/bin/pytest services/api/tests
	PYTHONPATH=services/worker .venv-worker/bin/pytest services/worker/tests
