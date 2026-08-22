PYTHON ?= python3
VENV ?= .venv
PIP_INDEX ?= https://pypi.org/simple

.PHONY: setup test lint init-db
setup:
	uv venv --python 3.13 $(VENV)
	uv pip install --python $(VENV)/bin/python --index-url $(PIP_INDEX) -e '.[dev]'

test:
	$(VENV)/bin/pytest -q

lint:
	$(VENV)/bin/ruff format --check .
	$(VENV)/bin/ruff check .

init-db:
	$(VENV)/bin/python -m src.cli init-db

