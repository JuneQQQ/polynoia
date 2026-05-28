.PHONY: help dev server web build test test-server test-web lint format types migrate clean install

help:
	@echo "Polynoia development commands:"
	@echo ""
	@echo "  make install        Install all dependencies (uv + pnpm)"
	@echo "  make dev            Run server + web in parallel"
	@echo "  make server         Run server only (uvicorn --reload)"
	@echo "  make web            Run web only (vite dev)"
	@echo "  make types          Generate TS types from Pydantic schemas"
	@echo "  make migrate        Run pending Alembic migrations"
	@echo "  make test           Run all tests"
	@echo "  make lint           Run linters (ruff + biome)"
	@echo "  make format         Auto-format code"
	@echo "  make build          Build server + web for production"
	@echo "  make clean          Remove build artifacts + caches"

install:
	# Backend: --extra dev pulls pytest/ruff/mypy so `make test` works
	cd apps/server && uv sync --extra dev
	# Frontend: auto-bootstrap pnpm via corepack(ships with Node 16+),
	# falls back to npm workspaces if corepack unavailable.
	@if command -v pnpm >/dev/null 2>&1; then \
		pnpm install; \
	elif command -v corepack >/dev/null 2>&1; then \
		echo "→ Activating pnpm via corepack..."; \
		corepack enable; \
		corepack prepare pnpm@9.0.0 --activate; \
		pnpm install; \
	else \
		echo "⚠ pnpm not found and corepack unavailable, falling back to npm"; \
		npm install; \
	fi

dev:
	@trap 'kill 0' INT; \
	$(MAKE) -j2 server web

server:
	cd apps/server && uv run uvicorn polynoia.main:app --reload --host 0.0.0.0 --port 7780

web:
	pnpm --filter @polynoia/web dev

build:
	cd apps/server && uv build
	pnpm --filter @polynoia/web build

types:
	cd apps/server && uv run python -m scripts.gen_ts_types ../../packages/shared/src/

test: test-server test-web

test-server:
	cd apps/server && uv run pytest

test-web:
	pnpm --filter @polynoia/web test

lint:
	cd apps/server && uv run ruff check .
	pnpm --filter @polynoia/web lint

format:
	cd apps/server && uv run ruff format .
	pnpm exec biome format --write .

migrate:
	cd apps/server && uv run alembic upgrade head

migration:
	cd apps/server && uv run alembic revision --autogenerate -m "$(name)"

clean:
	find . -type d -name '__pycache__' -exec rm -rf {} +
	find . -type d -name '.pytest_cache' -exec rm -rf {} +
	find . -type d -name '.ruff_cache' -exec rm -rf {} +
	find . -type d -name '.mypy_cache' -exec rm -rf {} +
	rm -rf apps/web/dist apps/web/.vite
	rm -rf apps/server/dist
	rm -rf node_modules/.cache
