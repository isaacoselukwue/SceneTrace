# SceneTrace developer commands. Created by Isaac Oselukwue.
# PYTHONPATH is set explicitly so unrelated global Python paths cannot leak into the tools.
PY := .venv/bin/python
export PYTHONPATH := $(CURDIR)
PORT ?= 8765

.PHONY: bootstrap dev test test-ui test-live demo evaluate build check secrets drift

bootstrap: ## Install locked Python and client dependencies
	uv sync --frozen --group dev
	cd client && npm ci --no-audit --no-fund

dev: ## Run the API on :$(PORT) and the Vite client on :5173 (proxying /api)
	PORT=$(PORT) tools/dev.sh

test: ## Unit, integration and fixture-provider tests. No paid calls.
	$(PY) -m pytest tests -o addopts="" -q
	$(PY) -m unittest discover -s reference_tests
	cd client && npm test

test-ui: ## Browser tests against the real server in fixture mode (system Chrome)
	cd client && npm run build && npx playwright test

test-live: ## Opt-in, capped live smoke test against Meta Model API (spends money)
	SCENETRACE_RUN_LIVE_TESTS=true $(PY) -m pytest tests/live -o addopts="" -q -s

demo: ## Serve approved recorded runs read-only, replayed without model calls
	cd client && npm run build
	SCENETRACE_DATA_DIR=.local/demo-data SCENETRACE_DEMO_READ_ONLY=true MODEL_API_KEY= \
		$(PY) -m scenetrace serve --port $(PORT) --import-demos demos

evaluate: ## Score completed real runs against held-out labels and run the fault-injection suite
	$(PY) -m scenetrace.evaluation.run

build: ## Build the client and the single-process application image
	cd client && npm run build
	docker build -t scenetrace:local .

check: secrets drift ## Lint, types, tests, schema drift and secret checks
	.venv/bin/ruff check scenetrace tests tools reference_core
	.venv/bin/pyrefly check
	cd client && npx tsc --noEmit
	$(MAKE) test

secrets: ## Fail if a credential appears in tracked files or the built client
	$(PY) tools/check_secrets.py

drift: ## Fail if generated contracts or client API types are stale
	$(PY) tools/export_schemas.py > /dev/null
	$(PY) tools/export_openapi.py > /dev/null
	cd client && npx openapi-typescript src/api/openapi.json -o src/api/schema.ts > /dev/null
	git diff --exit-code -- contracts client/src/api
