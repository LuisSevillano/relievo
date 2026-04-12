PYTHON ?= python3
PIP    ?= pip3

.PHONY: help install test test-fast lint format check package clean

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  install     Install package in editable mode + dev dependencies"
	@echo "  test        Run full test suite"
	@echo "  test-fast   Run tests, skip slow/integration tests"
	@echo "  lint        Run ruff linter"
	@echo "  format      Auto-format with ruff"
	@echo "  check       lint + test (CI equivalent)"
	@echo "  package     Build sdist/wheel and verify CLI from wheel"
	@echo "  clean       Remove build artifacts and __pycache__"

install:
	$(PIP) install -e ".[dev]" --quiet

test:
	$(PYTHON) -m pytest tests/ -v

test-fast:
	$(PYTHON) -m pytest tests/ -v -m "not slow"

lint:
	$(PYTHON) -m ruff check relievo/ tests/

format:
	$(PYTHON) -m ruff format relievo/ tests/

check: lint test

package:
	$(PYTHON) -m pip install --upgrade build
	$(PYTHON) -m build
	$(PYTHON) -m venv .venv-packaging
	. .venv-packaging/bin/activate && pip install --upgrade pip && pip install dist/*.whl && relievo --version

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .venv-packaging
