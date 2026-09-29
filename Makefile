.PHONY: e2e up down api web dev worker-interactive worker-bulk seed test test-int types types-check lint

up:
	docker compose -f deploy/docker-compose.yml up -d

down:
	docker compose -f deploy/docker-compose.yml down

api:
	cd backend && uv run uvicorn app.main:create_app --factory --reload

web:
	cd frontend && npm run dev

# API and frontend in one command; Ctrl-C stops both.
dev:
	$(MAKE) -j2 api web

worker-interactive:
	cd backend && uv run arq app.workers.main.WorkerInteractive

worker-bulk:
	cd backend && uv run arq app.workers.main.WorkerBulk

seed:
	cd backend && uv run python ../scripts/seed.py

test:
	cd backend && uv run pytest -m "not integration"

test-int:
	cd backend && uv run pytest

types:
	bash scripts/gen_types.sh

types-check: types
	git diff --exit-code -- frontend/src/api/schema.d.ts

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .

e2e:
	cd backend && uv run --frozen python ../scripts/e2e_jury.py
