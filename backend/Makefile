# Makefile - common development and operations tasks
# The database container is only used for the "compose" targets.

COMPOSE := docker compose
PY      := python3
VENV    ?= .venv

.PHONY: help install dev test test-unit bench migrate revision upgrade worker run clean

help:
	@echo "install    - install pinned dependencies into $(VENV)"
	@echo "dev        - run the API locally (SQLite, embedded worker)"
	@echo "worker     - run a standalone worker process"
	@echo "test       - run the full test suite"
	@echo "test-unit  - fast unit tests only"
	@echo "bench      - run the benchmark suite"
	@echo "upgrade    - apply Alembic migrations (AGP_DATABASE_URL)"
	@echo "revision   - autogenerate a migration (after model changes)"
	@echo "compose-up - build and start the full docker stack"

install:
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install -r requirements-dev.txt

dev:
	AGP_WORKER_EMBEDDED=true uvicorn --factory app.main:create_app --reload --port 8000

worker:
	$(PY) -m app.worker

run:
	uvicorn --factory app.main:create_app --port 8000

upgrade:
	alembic upgrade head

revision:
	alembic revision --autogenerate -m "$(m)"

test:
	$(PY) -m pytest tests/ -q

test-unit:
	$(PY) -m pytest tests/unit -q

bench:
	$(PY) scripts/benchmark.py

compose-up:
	$(COMPOSE) up -d --build

compose-logs:
	$(COMPOSE) logs -f api worker

compose-down:
	$(COMPOSE) down

clean:
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache data/*.db
