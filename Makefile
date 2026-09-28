.DEFAULT_GOAL := help
.PHONY: help up down logs services test lint fmt check superuser

help: ## List the available targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-10s %s\n", $$1, $$2}'

up: ## Build and start the whole stack (API on http://localhost:8000)
	docker compose up --build -d

down: ## Stop the stack (add `-v` to docker compose to also drop the database)
	docker compose down

logs: ## Follow the API logs
	docker compose logs -f api

services: ## Start only Postgres and Redis, for running the app or tests locally
	docker compose up -d db redis

test: ## Run the test suite with coverage (needs `make services`)
	uv run pytest --cov

lint: ## Lint and check formatting
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Format and auto-fix
	uv run ruff format .
	uv run ruff check --fix .

check: lint test ## Everything CI runs
	uv run python manage.py makemigrations --check --dry-run

superuser: ## Create an administrator inside the running stack
	docker compose exec api python manage.py createsuperuser
