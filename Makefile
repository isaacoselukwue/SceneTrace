PY := .venv/bin/python
export PYTHONPATH := $(CURDIR)
PORT ?= 8765

.PHONY: bootstrap dev test test-ui test-live demo evaluate build check secrets drift

bootstrap:
	uv sync --frozen --group dev
	cd client && npm ci --no-audit --no-fund

dev:
	PORT=$(PORT) tools/dev.sh

test:
	$(PY) -m pytest tests -o addopts="" -q
	$(PY) -m unittest discover -s reference_tests
	cd client && npm test

test-ui:
	cd client && npm run build && npx playwright test

test-live:
	SCENETRACE_RUN_LIVE_TESTS=true $(PY) -m pytest tests/live -o addopts="" -q -s

demo:
	cd client && npm run build
	SCENETRACE_DATA_DIR=.local/demo-data SCENETRACE_DEMO_READ_ONLY=true MODEL_API_KEY= \
		$(PY) -m scenetrace serve --port $(PORT) --import-demos demos

evaluate:
	$(PY) -m scenetrace.evaluation.run

build:
	cd client && npm run build
	docker build -t scenetrace:local .

check: secrets drift
	.venv/bin/ruff check scenetrace tests tools reference_core
	.venv/bin/pyrefly check
	cd client && npx tsc --noEmit
	$(MAKE) test

secrets:
	$(PY) tools/check_secrets.py

drift:
	$(PY) tools/export_schemas.py > /dev/null
	$(PY) tools/export_openapi.py > /dev/null
	cd client && npx openapi-typescript src/api/openapi.json -o src/api/schema.ts > /dev/null
	git diff --exit-code -- contracts client/src/api
