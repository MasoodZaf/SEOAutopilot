.PHONY: install dev infra-up infra-down migrate check

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

check:
	pnpm check
	.venv-api/bin/ruff check services/api
	.venv-api/bin/pyright --pythonpath .venv-api/bin/python services/api
	.venv-worker/bin/ruff check services/worker
	.venv-worker/bin/pyright --pythonpath .venv-worker/bin/python services/worker
	PYTHONPATH=services/api .venv-api/bin/pytest services/api/tests
	PYTHONPATH=services/worker .venv-worker/bin/pytest services/worker/tests
