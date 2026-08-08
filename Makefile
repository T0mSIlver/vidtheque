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
sync-gpu: ## Install the workspace with inference extras (whisperX, ST, RapidOCR, NVML)
	uv sync --package vidtheque-worker --extra gpu --extra nvml

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

.PHONY: images
images: ## Build both container images locally
	docker build -f worker/Dockerfile -t vidtheque-worker:dev .
	docker build -f mcp/Dockerfile -t vidtheque-mcp:dev .
