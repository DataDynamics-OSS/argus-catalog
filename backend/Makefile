.PHONY: dev run test lint format clean

dev:
	pip install -e ".[dev]"

run:
	ARGUS_CATALOG_SERVER_CONFIG_DIR=$(CURDIR)/packaging/config \
		uvicorn app.main:app --host 0.0.0.0 --port 4600 --reload --reload-dir app

test:
	pytest tests/ -v

lint:
	ruff check app/ tests/ && ruff format --check app/ tests/

format:
	ruff format app/ tests/ && ruff check --fix app/ tests/

clean:
	rm -rf build/ dist/ *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} +
