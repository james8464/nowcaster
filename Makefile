PYTHON ?= python3
VENV ?= .venv
PIP_INDEX ?= https://pypi.org/simple

.PHONY: setup test lint init-db fetch features train backtest dashboard dashboard-smoke report demo clean-generated macos-build macos-test macos-app macos-open
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

dashboard-smoke:
	$(VENV)/bin/python scripts/dashboard_smoke.py

report:
	$(VENV)/bin/python -m src.cli report --mode demo

demo:
	$(VENV)/bin/python -m src.cli demo

clean-generated:
	rm -f data/nowcaster.duckdb data/test.duckdb reports/latest_research_report.md reports/resume_bullets.md

macos-build:
	cd macos/Nowcaster && swift build

macos-test:
	cd macos/Nowcaster && swift test

macos-app:
	./scripts/build_macos_app.sh

macos-open: macos-app
	open build/Nowcaster.app
