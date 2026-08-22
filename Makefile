PYTHON ?= python3
VENV ?= .venv
PIP_INDEX ?= https://pypi.org/simple

.PHONY: setup test lint init-db fetch features train backtest dashboard report demo
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

fetch:
	$(VENV)/bin/python -m src.cli fetch-fundamentals --mode demo
	$(VENV)/bin/python -m src.cli fetch-prices --mode demo
	$(VENV)/bin/python -m src.cli fetch-altdata --mode demo

features:
	$(VENV)/bin/python -m src.cli build-features --mode demo

train:
	$(VENV)/bin/python -m src.cli train --mode demo

backtest:
	$(VENV)/bin/python -m src.cli backtest --mode demo

dashboard:
	$(VENV)/bin/streamlit run dashboard/app.py

report:
	$(VENV)/bin/python -m src.cli report --mode demo

demo:
	$(VENV)/bin/python -m src.cli demo
