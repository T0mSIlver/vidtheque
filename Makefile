.DEFAULT_GOAL := help
SHELL := /bin/bash

WORKER_URL ?= http://localhost:8081
SCENARIO ?= bench/scenarios/stt-backends.toml
BENCH_ARGS ?= --dry-run

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'

.PHONY: sync
sync: ## Install the workspace (CPU deps only)
	uv sync

.PHONY: sync-gpu
sync-gpu: ## Full workspace + inference extras in one venv (mcp, worker, whisperX, ST, NVML, dev)
	uv sync --group gpu

.PHONY: test
test: ## Run the test suite (CPU only, no model downloads)
	uv run pytest -q

.PHONY: lock
lock: ## Refresh uv.lock
	uv lock

.PHONY: openapi
openapi: ## Regenerate worker/openapi.json — the mcp/worker contract
	uv run python worker/scripts/export_openapi.py

.PHONY: run-worker
run-worker: ## Run the worker locally (needs `make sync-gpu` for real inference)
	uv run vidtheque-worker

.PHONY: bench
bench: ## Run a bench scenario (SCENARIO=… BENCH_ARGS='--out bench/runs/')
	uv run python bench/run.py $(SCENARIO) --worker-url $(WORKER_URL) $(BENCH_ARGS)

.PHONY: bench-list
bench-list: ## List bundled bench scenarios
	uv run python bench/run.py --list

.PHONY: web-check
web-check: ## Run every web/ check CI runs, in the same order
	cd web && pnpm install --frozen-lockfile
	cd web && pnpm tokens:check
	cd web && pnpm format:check
	cd web && pnpm lint
	cd web && pnpm test
	cd web && pnpm typecheck
	cd web && pnpm build

.PHONY: images
images: ## Build the dependency bases and three runnable images locally
	worker_base_tag="$$(scripts/image_inputs.sh worker --tag)"; \
	docker build -f worker/Dockerfile.base -t "vidtheque-worker-base:$$worker_base_tag" .; \
	docker build --build-arg "BASE_IMAGE=vidtheque-worker-base:$$worker_base_tag" -f worker/Dockerfile -t vidtheque-worker:dev .
	mcp_base_tag="$$(scripts/image_inputs.sh mcp --tag)"; \
	docker build -f mcp/Dockerfile.base -t "vidtheque-mcp-base:$$mcp_base_tag" .; \
	docker build --build-arg "BASE_IMAGE=vidtheque-mcp-base:$$mcp_base_tag" -f mcp/Dockerfile -t vidtheque-mcp:dev .
	docker build -f web/Dockerfile -t vidtheque-web:dev web
