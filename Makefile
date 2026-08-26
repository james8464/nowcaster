PYTHON ?= python3
VENV ?= .venv
PIP_INDEX ?= https://pypi.org/simple

.PHONY: setup test lint init-db fetch features train backtest report demo research-ci research-live research-live-probe verify-research-fixtures verify-swift-fixture-parity verify-deep-research verify-paper-trading verify-trading-readiness verify-live-monitor replay-live-monitor secret-scan clean-generated sync-macos-snapshot engine-bundle macos-build macos-test macos-app macos-open macos-ui-test macos-screenshots release-archive
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

research-live:
	test -n "$(CACHE_DIR)"
	mkdir -p build/research-live
	rm -f build/research-live/research.duckdb build/research-live/research.duckdb.wal
	$(VENV)/bin/python -m src.cli strategy research --profile live --output-dir build/research-live --cache-dir "$(CACHE_DIR)" --cutoff 2026-08-24T00:00:00Z

research-live-probe:
	test -n "$(CACHE_DIR)"
	mkdir -p build
	rm -f build/research-live-probe.duckdb build/research-live-probe.duckdb.wal
	$(VENV)/bin/python -m src.cli strategy research --profile live --database-url duckdb:///build/research-live-probe.duckdb --output-dir build/research-live-probe --cache-dir "$(CACHE_DIR)" --cutoff 2026-08-24T00:00:00Z --max-chunks-per-scope 1

verify-research-fixtures: research-ci
	$(VENV)/bin/python -c 'from pathlib import Path; from src.app_snapshot.models import AppSnapshot; snapshot = AppSnapshot.model_validate_json(Path("data/research/ci/nowcaster-snapshot.json").read_text()); assert snapshot.schema_version == 5'
	git diff --exit-code -- data/research/ci

verify-swift-fixture-parity:
	$(VENV)/bin/python scripts/verify_snapshot_fixture_parity.py --python-research data/research/ci/nowcaster-snapshot.json --swift-fixture macos/Nowcaster/Sources/NowcasterApp/Resources/Fixtures/nowcaster-snapshot.json

secret-scan:
	$(VENV)/bin/python scripts/scan_tracked_secrets.py

verify-deep-research:
	$(VENV)/bin/pytest -q tests/integration/test_deep_research_end_to_end.py tests/integration/test_deep_research_coordinator.py tests/integration/test_deep_research_pipeline.py

sync-macos-snapshot:
	$(VENV)/bin/python -m src.cli export-app-snapshot --output data/app/nowcaster-snapshot.json
	$(VENV)/bin/python scripts/synchronize_snapshot_fixture.py --base data/app/nowcaster-snapshot.json --research data/research/ci/nowcaster-snapshot.json --output macos/Nowcaster/Sources/NowcasterApp/Resources/Fixtures/nowcaster-snapshot.json

clean-generated:
	rm -f data/nowcaster.duckdb data/test.duckdb data/app/nowcaster-snapshot.json reports/latest_research_report.md

macos-build:
	cd macos/Nowcaster && swift build

engine-bundle:
	./scripts/build_engine_bundle.sh

verify-paper-trading:
	$(VENV)/bin/pytest -q tests/unit/test_trading_types.py tests/unit/test_shadow_broker.py tests/unit/test_alpaca_trading.py tests/unit/test_trade_update_stream.py tests/integration/test_trading_repository.py tests/integration/test_trading_supervisor.py tests/integration/test_trading_cli.py

verify-trading-readiness:
	$(VENV)/bin/pytest -q tests/unit/test_trading_risk.py tests/integration/test_trading_emergency.py tests/unit/test_forward_evidence.py tests/unit/test_live_readiness.py tests/unit/test_live_broker_lock.py tests/unit/test_live_arming.py

replay-live-monitor:
	$(VENV)/bin/python -c 'import json; print(json.dumps({"schema_version": 1, "session_id": "deterministic-replay", "database_url": "duckdb:///:memory:", "stock_feed": "iex", "stocks": [], "crypto": ["BTCUSDT"], "decision_interval": "5m", "config_hash": "c" * 64, "cohort_hash": "d" * 64}))' | $(VENV)/bin/python -m src.cli monitor run --replay tests/fixtures/live_monitor/binance_stream.jsonl --replay-provider binance

verify-live-monitor:
	$(VENV)/bin/pytest -q tests/unit/test_live_monitor_*.py tests/integration/test_live_monitor_*.py tests/unit/test_engine_packaging.py
	cd macos/Nowcaster && swift test --filter LiveMonitor
	$(MAKE) replay-live-monitor

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
	./scripts/verify_production_release.sh build/Nowcaster.app
	cd build && ditto -c -k --sequesterRsrc --keepParent Nowcaster.app Nowcaster-macOS.zip
	cd build && shasum -a 256 Nowcaster-macOS.zip > Nowcaster-macOS.zip.sha256
