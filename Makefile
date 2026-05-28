.PHONY: help dev server web build test test-server test-web lint format types migrate clean install

# Auto-detect the JS package manager. Prefer pnpm (canonical), fall back
# to npm workspaces (npm 7+ supports the same package.json layout). All
# JS-side targets go through $(PM_WEB) so team members without pnpm can
# still `make dev`.
ifneq (,$(shell command -v pnpm 2>/dev/null))
  PM_WEB  := pnpm --filter @polynoia/web
  PM_EXEC := pnpm exec
else
  PM_WEB  := npm --workspace=@polynoia/web run
  PM_EXEC := npx
endif

help:
	@echo "Polynoia development commands:"
	@echo ""
	@echo "  make install        Install all dependencies (uv + pnpm/npm)"
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
	# Frontend: prefer pnpm; fall back to npm workspaces if pnpm isn't
	# installed (npm 7+ natively supports the same workspace layout).
	@if command -v pnpm >/dev/null 2>&1; then \
		pnpm install; \
	else \
		echo "ℹ pnpm not found, using npm workspaces (slower but works)"; \
		echo "  for the canonical experience: 'npm i -g pnpm' before next time"; \
		npm install; \
	fi

dev:
	@trap 'kill 0' INT; \
	$(MAKE) -j2 server web

server:
	cd apps/server && uv run uvicorn polynoia.main:app --reload --host 0.0.0.0 --port 7780

web:
	$(PM_WEB) dev

build:
	cd apps/server && uv build
	$(PM_WEB) build

types:
	cd apps/server && uv run python -m scripts.gen_ts_types ../../packages/shared/src/

test: test-server test-web

test-server:
	cd apps/server && uv run pytest

test-web:
	$(PM_WEB) test

lint:
	cd apps/server && uv run ruff check .
	$(PM_WEB) lint

format:
	cd apps/server && uv run ruff format .
	$(PM_EXEC) biome format --write .

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
