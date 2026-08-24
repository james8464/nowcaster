PYTHON ?= python3
VENV ?= .venv
PIP_INDEX ?= https://pypi.org/simple

.PHONY: setup test lint init-db fetch features train backtest report demo research-ci research-live-probe verify-research-fixtures secret-scan clean-generated sync-macos-snapshot macos-build macos-test macos-app macos-open macos-ui-test macos-screenshots release-archive
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

report:
	$(VENV)/bin/python -m src.cli report --mode demo

demo:
	$(VENV)/bin/python -m src.cli demo

research-ci:
	mkdir -p build data/research/ci
	rm -f build/research-ci.duckdb build/research-ci.duckdb.wal
	$(VENV)/bin/python -m src.cli strategy research --profile ci --database-url duckdb:///build/research-ci.duckdb --output-dir data/research/ci

research-live-probe:
	test -n "$(CACHE_DIR)"
	mkdir -p build
	rm -f build/research-live-probe.duckdb build/research-live-probe.duckdb.wal
	$(VENV)/bin/python -m src.cli strategy research --profile live --database-url duckdb:///build/research-live-probe.duckdb --output-dir build/research-live-probe --cache-dir "$(CACHE_DIR)" --cutoff 2026-08-24T00:00:00Z --max-chunks-per-scope 1

verify-research-fixtures: research-ci
	$(VENV)/bin/python -c 'from pathlib import Path; from src.app_snapshot.models import AppSnapshot; snapshot = AppSnapshot.model_validate_json(Path("data/research/ci/nowcaster-snapshot.json").read_text()); assert snapshot.schema_version == 2'
	git diff --exit-code -- data/research/ci

secret-scan:
	$(VENV)/bin/python scripts/scan_tracked_secrets.py

sync-macos-snapshot:
	$(VENV)/bin/python -m src.cli export-app-snapshot --output macos/Nowcaster/Sources/NowcasterApp/Resources/Fixtures/nowcaster-snapshot.json

clean-generated:
	rm -f data/nowcaster.duckdb data/test.duckdb data/app/nowcaster-snapshot.json reports/latest_research_report.md

macos-build:
	cd macos/Nowcaster && swift build

macos-test:
	cd macos/Nowcaster && swift test

macos-app:
	./scripts/build_macos_app.sh

macos-open: macos-app
	open build/Nowcaster.app

macos-ui-test: macos-app
	xcrun swift scripts/capture_macos_app.swift build/Nowcaster.app /tmp/nowcaster-ui-smoke --verify-only

macos-screenshots: macos-app
	xcrun swift scripts/capture_macos_app.swift build/Nowcaster.app docs/images/macos

release-archive: macos-app
	cd build && ditto -c -k --sequesterRsrc --keepParent Nowcaster.app Nowcaster-macOS.zip
	cd build && shasum -a 256 Nowcaster-macOS.zip > Nowcaster-macOS.zip.sha256
