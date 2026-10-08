.DEFAULT_GOAL := help

.PHONY: help install lint fmt test run run-mock api migrate up down logs docker-build

help: ## List available targets
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  %-14s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

install: ## Install dependencies with uv
	uv sync

lint: ## Run ruff lint and format checks
	uv run ruff check .
	uv run ruff format --check .

fmt: ## Auto-format and fix lint issues
	uv run ruff format .
	uv run ruff check --fix .

test: ## Run tests with coverage gate
	uv run pytest -q --cov=docpipe --cov-report=term-missing --cov-fail-under=80

run: ## Run one pipeline pass
	uv run docpipe run

run-mock: ## Run one pipeline pass with mock data
	uv run docpipe run --mock

api: ## Start the FastAPI server
	uv run docpipe api

migrate: ## Apply database migrations
	uv run alembic upgrade head

up: ## Build and start the docker compose stack
	docker compose up --build -d

down: ## Stop the docker compose stack
	docker compose down

logs: ## Follow runner and api logs
	docker compose logs -f runner api

docker-build: ## Build the local docker image
	docker build -t docpipe:local .
