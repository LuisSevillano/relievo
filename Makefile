PYTHON := /Library/Frameworks/Python.framework/Versions/3.9/bin/python3.9
PIP    := /Library/Frameworks/Python.framework/Versions/3.9/bin/pip3.9

.PHONY: help install test test-fast lint format check clean

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  install     Install package in editable mode + dev dependencies"
	@echo "  test        Run full test suite"
	@echo "  test-fast   Run tests, skip slow/integration tests"
	@echo "  lint        Run ruff linter"
	@echo "  format      Auto-format with ruff"
	@echo "  check       lint + test (CI equivalent)"
	@echo "  clean       Remove build artifacts and __pycache__"

install:
	$(PIP) install -e ".[dev]" --quiet

test:
	$(PYTHON) -m pytest tests/ -v

test-fast:
	$(PYTHON) -m pytest tests/ -v -m "not slow"

lint:
	$(PYTHON) -m ruff check blender_relief/ tests/

format:
	$(PYTHON) -m ruff format blender_relief/ tests/

check: lint test

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
