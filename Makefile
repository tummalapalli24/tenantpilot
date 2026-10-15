# TenantPilot developer commands. `make help` lists them.
SHELL := /bin/bash
BASE_URL ?= http://localhost:8000

.PHONY: help
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------- one-command stacks
.PHONY: up up-obs down clean
up: ## Full stack in Docker (UI http://localhost:3000)
	docker compose up --build -d
	@echo "UI: http://localhost:3000  API docs: http://localhost:8000/api/docs  login: admin@tenantpilot.dev / tenantpilot-demo"

up-obs: ## Full stack + Jaeger (16686) + Prometheus (9090) + Grafana (3001)
	OTEL_EXPORTER_OTLP_ENDPOINT=http://jaeger:4318 docker compose --profile observability up --build -d

down: ## Stop the Docker stack (keeps data)
	docker compose --profile observability down

clean: ## Stop the Docker stack and delete its volumes
	docker compose --profile observability down -v

# ---------------------------------------------------------------- local development (hot reload)
.PHONY: install infra migrate seed api mcp mock worker web
install: ## Install Python (uv) and web (npm) dependencies
	uv sync
	cd apps/web && npm ci

infra: ## Start only Postgres + Redis in Docker
	docker compose up -d postgres redis

migrate: ## Bootstrap DB roles + run migrations
	uv run python -m apps.api.cli migrate

seed: ## Load demo tenants, users, connectors and knowledge
	uv run python -m apps.api.cli seed-demo

api: ## Run the API with reload (terminal 1)
	LOG_FORMAT=text RELOAD=1 uv run python -m apps.api

mcp: ## Run the MCP server (terminal 2)
	LOG_FORMAT=text uv run python -m services.mcp_server

mock: ## Run the mock customer API (terminal 3)
	uv run python -m services.mock_customer_api

worker: ## Run the ingestion worker (terminal 4; or set INGEST_MODE=inline for the API)
	LOG_FORMAT=text uv run python -m services.worker

web: ## Run the Next.js UI with hot reload (terminal 5)
	cd apps/web && npm run dev

# ---------------------------------------------------------------- quality gates
.PHONY: lint format test test-unit test-integration evals check-migrations
lint: ## Ruff + web typecheck/lint
	uv run ruff check .
	uv run ruff format --check .
	cd apps/web && npx next typegen >/dev/null && npx tsc --noEmit && npm run lint

format: ## Auto-format Python
	uv run ruff format .
	uv run ruff check --fix .

test: ## All tests (integration needs `make infra`)
	uv run pytest -q

test-unit: ## Unit tests only (no infrastructure needed)
	uv run pytest tests/unit -q

test-integration: ## Integration, security and failure tests
	uv run pytest tests/integration -q

evals: ## Agent evaluation suite against BASE_URL
	uv run python -m evals.run_evals --base-url $(BASE_URL)

check-migrations: ## Fail if ORM models and migrations drift apart
	uv run alembic check

# ---------------------------------------------------------------- kubernetes + infra
.PHONY: kind-up kind-down helm-lint tf-validate
kind-up: ## Local Kubernetes (kind + Traefik + Helm), UI http://localhost:8080
	./scripts/kind-up.sh

kind-down: ## Delete the kind cluster
	./scripts/kind-down.sh

helm-lint: ## Lint + schema-validate the chart for every environment
	@for v in values.yaml values-kind.yaml values-prod.yaml; do \
	  helm lint helm/tenantpilot -f helm/tenantpilot/$$v --quiet && \
	  helm template tp helm/tenantpilot -f helm/tenantpilot/$$v | kubeconform -strict -summary -kubernetes-version 1.33.0 - ; \
	done

tf-validate: ## Terraform/OpenTofu fmt + validate (no AWS credentials needed)
	cd infra/terraform && tofu fmt -check -recursive && tofu init -backend=false -input=false >/dev/null && tofu validate
