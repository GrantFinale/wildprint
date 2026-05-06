# Wildprint developer Makefile (Phase 0.7)
# All targets are phony; nothing here generates files in-tree.

.PHONY: help install test test-integration test-all lint typecheck cov cov-html clean all

PYTHON ?= python
PIP    ?= pip

help:
	@echo "wildprint dev targets:"
	@echo "  make install           install runtime + dev deps"
	@echo "  make test              run unit tests (skips @integration)"
	@echo "  make test-integration  run integration tests (needs DATABASE_URL/REDIS_URL)"
	@echo "  make test-all          run unit + integration"
	@echo "  make lint              ruff check on review_app/"
	@echo "  make typecheck         mypy strict on review_app/"
	@echo "  make cov               pytest with coverage (terminal)"
	@echo "  make cov-html          pytest with coverage (HTML report in htmlcov/)"
	@echo "  make all               lint + typecheck + test"
	@echo "  make clean             remove caches and coverage artifacts"

install:
	$(PIP) install -r requirements.txt -r requirements-dev.txt

test:
	pytest

test-integration:
	pytest --integration

test-all:
	pytest --integration

lint:
	ruff check review_app/

lint-fix:
	ruff check --fix review_app/
	ruff format review_app/

typecheck:
	mypy review_app/

cov:
	pytest --cov --cov-report=term-missing

cov-html:
	pytest --cov --cov-report=term-missing --cov-report=html
	@echo "HTML report: htmlcov/index.html"

all: lint typecheck test

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov
	rm -f .coverage coverage.xml
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
