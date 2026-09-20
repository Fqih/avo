.PHONY: install install-global setup dev lint format typecheck test test-app-tools benchmark clean build

PYTHON ?= python

install:
	$(PYTHON) -m pip install -e .

install-global:
	$(PYTHON) -m pip install .

setup:
	avo setup --global

dev:
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check .

format:
	$(PYTHON) -m ruff format .

format-check:
	$(PYTHON) -m ruff format --check .

typecheck:
	$(PYTHON) -m mypy src/avo

test:
	$(PYTHON) -m pytest

test-app-tools:
	$(PYTHON) -m pytest tests/test_workspace.py tests/test_approval.py tests/test_file_tools.py -v

security-check:
	bandit -r src/avo -c pyproject.toml --severity-level medium

coverage:
	$(PYTHON) -m pytest --cov=avo --cov-report=term-missing

ci: lint format-check typecheck security-check test

benchmark:
	$(PYTHON) benchmark/run_benchmark.py

build:
	$(PYTHON) -m build

clean:
	rm -rf build dist *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true

all-gates: ci