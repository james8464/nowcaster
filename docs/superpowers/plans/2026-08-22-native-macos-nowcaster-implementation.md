# Native macOS Nowcaster Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the website-first product with a native, accessible SwiftUI macOS research workstation backed by stronger point-in-time equity and crypto models and complete walk-forward backtests.

**Architecture:** Python remains the source of truth for ingestion, modelling, backtests, and provenance. It exports a validated, atomic, versioned JSON snapshot consumed by a SwiftUI macOS app; the app can safely invoke explicit Python CLI jobs through `Process`. Equity earnings signals and crypto time-series signals remain separate research systems but share evidence, calibration, abstention, and backtest presentation contracts.

**Tech Stack:** Python 3.11–3.13, pandas, scikit-learn, statsmodels, DuckDB, Pydantic, Typer, Swift 6.3, SwiftUI, Swift Charts, Observation, Swift Testing, Xcode 26.6, GitHub Actions macOS runners.

**Spec:** `docs/superpowers/specs/2026-08-22-native-macos-nowcaster-design.md`

## Global Constraints

- Development uses Swift 6.3 and macOS 26 SDK; deployment target is macOS 15.0.
- The macOS UI contains no `WebView`, embedded browser, Streamlit server, or local HTTP dependency.
- Python and Swift communicate only through the versioned `nowcaster-snapshot.json` contract and structured process progress.
- Equity and crypto models, labels, signal terminology, portfolios, and results remain separate.
- Labels become trainable only after their real availability timestamp; every transform fits within its training fold.
- No UI copy may equate confidence with profit probability or imply guaranteed returns.
- System typography, semantic colors/materials, SF Symbols, standard navigation/toolbars/tables/settings, keyboard access, VoiceOver, reduced motion, and increased contrast are mandatory.
- The app executes no brokerage orders and stores no credentials in Git, logs, DuckDB exports, or app snapshots.
- Every task follows red-green-refactor TDD and ends with a focused commit.

---

### Task 1: Native Swift package and deterministic app bundle

**Files:**
- Create: `macos/Nowcaster/Package.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/NowcasterApp.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/AppDestination.swift`
- Create: `macos/Nowcaster/Tests/NowcasterAppTests/AppDestinationTests.swift`
- Create: `macos/Nowcaster/Resources/Info.plist`
- Create: `scripts/build_macos_app.sh`
- Modify: `Makefile`

**Interfaces:**
- Consumes: macOS 15+ SwiftUI runtime.
- Produces: `AppDestination: String, CaseIterable, Identifiable`, `NowcasterApp`, `build/Nowcaster.app`, and `make macos-build|macos-test|macos-app|macos-open`.

- [x] **Step 1: Write the failing Swift destination test**

```swift
import Testing
@testable import NowcasterApp

@Test func destinationsHaveStableUniqueIdentifiers() {
    let ids = AppDestination.allCases.map(\.id)
    #expect(Set(ids).count == ids.count)
    #expect(AppDestination.today.title == "Today")
    #expect(AppDestination.backtests.symbolName == "chart.xyaxis.line")
}
```

- [x] **Step 2: Run the test to verify the package is absent**

Run: `cd macos/Nowcaster && swift test`
Expected: FAIL because `Package.swift` and `AppDestination` do not exist.

- [x] **Step 3: Implement the package, destinations, minimal app, and bundle builder**

```swift
public enum AppDestination: String, CaseIterable, Identifiable, Sendable {
    case today, markets, earnings, signals, backtests, modelLab, dataQuality, pipelineRuns
    public var id: String { rawValue }
    public var title: String { switch self { case .today: "Today"; case .modelLab: "Model Lab"; case .dataQuality: "Data Quality"; case .pipelineRuns: "Pipeline Runs"; default: rawValue.capitalized } }
    public var symbolName: String { switch self { case .today: "sparkles"; case .markets: "chart.line.uptrend.xyaxis"; case .earnings: "calendar.badge.clock"; case .signals: "waveform.path.ecg"; case .backtests: "chart.xyaxis.line"; case .modelLab: "slider.horizontal.3"; case .dataQuality: "checkmark.shield"; case .pipelineRuns: "clock.arrow.trianglehead.counterclockwise.rotate.90" } }
}
```

The build script must call `swift build -c release`, assemble `Contents/MacOS`, `Contents/Resources`, and `Contents/Info.plist`, then run `codesign --force --deep --sign "${NOWCASTER_CODESIGN_IDENTITY:--}"` without using an unresolved path.

- [x] **Step 4: Run focused Swift verification**

Run: `make macos-test && make macos-app && codesign --verify --deep --strict build/Nowcaster.app`
Expected: tests pass and the app bundle verifies.

- [x] **Step 5: Commit**

```bash
git add macos/Nowcaster scripts/build_macos_app.sh Makefile
git commit -m "feat: establish native macOS app"
```

### Task 2: Versioned Python-to-Swift snapshot contract

**Files:**
- Create: `src/app_snapshot/__init__.py`
- Create: `src/app_snapshot/models.py`
- Create: `src/app_snapshot/builder.py`
- Create: `src/app_snapshot/writer.py`
- Create: `tests/unit/test_app_snapshot.py`
- Create: `tests/integration/test_app_snapshot_export.py`
- Modify: `src/cli.py`

**Interfaces:**
- Consumes: `Database`, recruiter statistics, model/backtest tables.
- Produces: `AppSnapshot`, `build_app_snapshot(database, settings) -> AppSnapshot`, `write_snapshot_atomic(snapshot, path) -> Path`, and CLI `export-app-snapshot`.

- [ ] **Step 1: Write failing schema and atomic-write tests**

```python
def test_snapshot_contract_is_versioned_and_never_calls_confidence_profit_probability(database):
    snapshot = build_app_snapshot(database, settings)
    assert snapshot.schema_version == 1
    assert snapshot.metadata.data_mode in {"demo_real_snapshot", "live_provider"}
    assert "probability of profit" not in snapshot.model_dump_json().lower()

def test_atomic_writer_replaces_complete_document(tmp_path, snapshot):
    path = write_snapshot_atomic(snapshot, tmp_path / "nowcaster-snapshot.json")
    assert AppSnapshot.model_validate_json(path.read_text()).schema_version == 1
    assert not list(tmp_path.glob("*.tmp"))
```

- [ ] **Step 2: Verify red**

Run: `.venv/bin/pytest tests/unit/test_app_snapshot.py tests/integration/test_app_snapshot_export.py -q`
Expected: FAIL because `src.app_snapshot` is missing.

- [ ] **Step 3: Implement strict Pydantic models and exporter**

Define focused nested models for `SnapshotMetadata`, `OverviewSnapshot`, `InstrumentSnapshot`, `PricePoint`, `EarningsSnapshot`, `ResearchSignalSnapshot`, `BacktestSnapshot`, `BacktestPoint`, `ModelDiagnosticSnapshot`, `QualityIssueSnapshot`, and `PipelineRunSnapshot`. Set `model_config = ConfigDict(extra="forbid")`. Serialize dates as ISO-8601 and nonfinite numbers as `None`. Write using `NamedTemporaryFile(dir=path.parent, delete=False)` followed by `Path.replace`.

- [ ] **Step 4: Add CLI command and run tests**

```python
@app.command("export-app-snapshot")
def export_app_snapshot(...):
    snapshot = build_app_snapshot(database, settings)
    path = write_snapshot_atomic(snapshot, output)
    typer.echo(json.dumps({"event": "snapshot_exported", "path": str(path), "schema_version": 1}))
```

Run: `.venv/bin/pytest tests/unit/test_app_snapshot.py tests/integration/test_app_snapshot_export.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/app_snapshot src/cli.py tests/unit/test_app_snapshot.py tests/integration/test_app_snapshot_export.py
git commit -m "feat: export native app snapshots"
```

### Task 3: Positive equity targets, ensembles, calibration, and abstention

**Files:**
- Create: `src/models/targets.py`
- Create: `src/models/ensemble.py`
- Create: `src/models/calibration.py`
- Create: `src/models/eligibility.py`
- Create: `tests/unit/test_model_targets.py`
- Create: `tests/unit/test_model_ensemble.py`
- Create: `tests/unit/test_model_calibration.py`
- Create: `tests/unit/test_signal_eligibility.py`
- Modify: `src/models/validation.py`
- Modify: `src/demo.py`

**Interfaces:**
- Produces: `encode_revenue_growth(actual, year_ago)`, `decode_revenue_growth(growth, year_ago)`, `inverse_error_weights(metrics)`, `RollingProbabilityCalibrator`, and `assess_signal_eligibility(...) -> EligibilityAssessment`.

- [ ] **Step 1: Write failing numerical and eligibility tests**

```python
def test_log_growth_round_trip_is_positive():
    growth = encode_revenue_growth(125.0, 100.0)
    assert decode_revenue_growth(growth, 100.0) == pytest.approx(125.0)
    assert decode_revenue_growth(-100.0, 100.0) > 0

def test_inverse_error_weights_use_only_prior_fold_metrics():
    assert inverse_error_weights({"ridge": 2.0, "elastic_net": 1.0}) == pytest.approx({"ridge": 1 / 3, "elastic_net": 2 / 3})

def test_wide_interval_forces_abstention():
    result = assess_signal_eligibility(interval_width_ratio=0.8, model_agreement=0.9, completeness=1, extrapolation=0, observations=100)
    assert result.posture == "abstain"
    assert "interval" in result.reasons[0].lower()
```

- [ ] **Step 2: Verify red**

Run: `.venv/bin/pytest tests/unit/test_model_targets.py tests/unit/test_model_ensemble.py tests/unit/test_model_calibration.py tests/unit/test_signal_eligibility.py -q`
Expected: import failures.

- [ ] **Step 3: Implement fold-local target modelling and diagnostics**

Use `log(actual / year_ago)` as the learned target and `year_ago * exp(prediction)` for reconstruction. Clip growth to training-fold quantiles, not revenue after prediction. Compute ensemble weights from prior-fold MAE only. Fit rolling isotonic calibration only after 100 direction observations with both classes; otherwise emit `calibration_status="insufficient"`. Abstain if configured gates fail.

- [ ] **Step 4: Integrate without cross-horizon leakage and run model suite**

Run: `.venv/bin/pytest tests/unit/test_model_*.py tests/unit/test_signal_eligibility.py tests/integration/test_training_pipeline.py -q`
Expected: PASS, and every persisted learned forecast is positive.

- [ ] **Step 5: Commit**

```bash
git add src/models src/demo.py tests/unit tests/integration/test_training_pipeline.py
git commit -m "feat: calibrate leakage-safe equity forecasts"
```

### Task 4: Crypto ingestion and time-series research pipeline

**Files:**
- Create: `config/instruments.yaml`
- Create: `src/crypto/__init__.py`
- Create: `src/crypto/features.py`
- Create: `src/crypto/models.py`
- Create: `src/crypto/pipeline.py`
- Create: `tests/unit/test_crypto_features.py`
- Create: `tests/unit/test_crypto_models.py`
- Create: `tests/integration/test_crypto_pipeline.py`
- Add: `data/demo/crypto/BTC-USD.json`
- Add: `data/demo/crypto/ETH-USD.json`
- Add: `data/demo/crypto/manifest.json`
- Modify: `src/config/settings.py`
- Modify: `src/demo.py`

**Interfaces:**
- Produces: `build_crypto_features(prices) -> DataFrame`, `make_crypto_walk_forward_folds(...)`, `run_crypto_models(...)`, and persisted crypto instruments/signals/backtests.

- [ ] **Step 1: Write failing feature chronology and execution-lag tests**

```python
def test_crypto_features_use_only_prior_closes(price_frame):
    features = build_crypto_features(price_frame)
    changed = price_frame.copy()
    changed.loc[changed.trading_date > features.iloc[20].decision_date, "adjusted_close"] *= 10
    rebuilt = build_crypto_features(changed)
    pd.testing.assert_series_equal(features.iloc[20], rebuilt.iloc[20])

def test_crypto_return_is_realized_after_decision(crypto_matrix):
    folds = make_crypto_walk_forward_folds(crypto_matrix, horizon_days=5, embargo_days=5)
    assert all(fold.training_label_end < fold.test_decision_start for fold in folds)
```

- [ ] **Step 2: Verify red**

Run: `.venv/bin/pytest tests/unit/test_crypto_features.py tests/unit/test_crypto_models.py tests/integration/test_crypto_pipeline.py -q`
Expected: missing module failures.

- [ ] **Step 3: Implement separate crypto models and demo snapshots**

Parse BTC-USD and ETH-USD adjusted daily prices through the existing price normalizer. Feature every row using shifted inputs. Baselines are flat, 20/100-day trend, and 20-day time-series momentum. Learned models are regularized logistic regression for direction and histogram gradient boosting for forward volatility-adjusted return, each gated by training sample and calibrated only with past predictions.

- [ ] **Step 4: Verify deterministic crypto research output**

Run: `.venv/bin/pytest tests/unit/test_crypto_features.py tests/unit/test_crypto_models.py tests/integration/test_crypto_pipeline.py -q`
Expected: PASS with BTC-USD and ETH-USD outputs and no future mutation effect.

- [ ] **Step 5: Commit**

```bash
git add config/instruments.yaml data/demo/crypto src/crypto src/config/settings.py src/demo.py tests
git commit -m "feat: add point-in-time crypto research"
```

### Task 5: Purged backtests, realistic portfolio accounting, and robustness

**Files:**
- Create: `src/backtest/protocol.py`
- Create: `src/backtest/metrics.py`
- Create: `src/backtest/robustness.py`
- Create: `src/backtest/readiness.py`
- Create: `tests/unit/test_backtest_protocol.py`
- Create: `tests/unit/test_backtest_metrics.py`
- Create: `tests/unit/test_backtest_robustness.py`
- Create: `tests/unit/test_backtest_readiness.py`
- Modify: `src/backtest/portfolio.py`
- Modify: `src/backtest/statistics.py`
- Modify: `src/database/schema.py`
- Modify: `src/demo.py`

**Interfaces:**
- Produces: `WalkForwardProtocol`, `BacktestMetrics`, `run_block_bootstrap`, `benjamini_hochberg`, `deflated_sharpe_probability`, `evaluate_readiness`, and normalized backtest run/curve/position/sensitivity tables.

- [ ] **Step 1: Write failing split, metric, and robustness tests**

```python
def test_protocol_reserves_final_test_and_embargoes_overlap(rows):
    protocol = WalkForwardProtocol(final_test_fraction=0.2, minimum_train=100, embargo=5)
    folds = protocol.split(rows, decision_column="decision_date", label_end_column="label_end")
    assert all(fold.train_label_end < fold.validation_start for fold in folds)
    assert min(protocol.final_test_indices) > max(protocol.development_indices)

def test_costs_and_one_bar_lag_reduce_crypto_return(position_frame):
    result = simulate_crypto_portfolio(position_frame, fee_bps=10, slippage_bps=5, target_volatility=0.15)
    assert result.net_cumulative_return < result.gross_cumulative_return
    assert result.positions.iloc[0].execution_date > result.positions.iloc[0].decision_date

def test_false_discovery_adjustment_is_monotonic():
    adjusted = benjamini_hochberg([0.01, 0.04, 0.2])
    assert adjusted == sorted(adjusted)
```

- [ ] **Step 2: Verify red**

Run: `.venv/bin/pytest tests/unit/test_backtest_protocol.py tests/unit/test_backtest_metrics.py tests/unit/test_backtest_robustness.py tests/unit/test_backtest_readiness.py -q`
Expected: missing module failures.

- [ ] **Step 3: Implement protocol, accounting, metrics, and schema**

Implement final-test isolation, purged expanding folds, horizon embargo, one-bar execution lag, exposure caps, volatility targeting, transaction/borrow/slippage costs, and one declared equity model/horizon per company-event. Persist CAGR, annualized return/volatility, Sharpe, Sortino, Calmar, drawdown, hit rate, profit factor, turnover, exposure, trades, and holding period. Run event/date block bootstrap, HAC/date-clustered inference, false-discovery correction, deflated Sharpe, leave-one-out, subperiod, regime, and cost sensitivity.

- [ ] **Step 4: Run full backtest unit and integration suites**

Run: `.venv/bin/pytest tests/unit/test_backtest_*.py tests/unit/test_portfolio.py tests/integration/test_backtest_pipeline.py -q`
Expected: PASS and readiness fails when sample, stability, cost, or final-test gates fail.

- [ ] **Step 5: Commit**

```bash
git add src/backtest src/database/schema.py src/demo.py tests
git commit -m "feat: harden walk-forward backtests"
```

### Task 6: Complete demo orchestration and native snapshot population

**Files:**
- Modify: `src/demo.py`
- Modify: `src/pipeline.py`
- Modify: `src/app_snapshot/builder.py`
- Modify: `src/reporting/research_report.py`
- Modify: `tests/integration/test_demo.py`
- Modify: `tests/integration/test_report_pipeline.py`
- Create: `tests/integration/test_native_snapshot_demo.py`

**Interfaces:**
- Consumes: completed equity, crypto, backtest, and snapshot services.
- Produces: one restartable `make demo` that rebuilds every native-app dataset and exports the final snapshot.

- [ ] **Step 1: Write failing end-to-end native demo contract**

```python
def test_demo_populates_native_equity_crypto_and_backtest_sections(demo_database, tmp_path):
    settings, database = demo_database
    snapshot = build_app_snapshot(database, settings)
    assert {item.asset_class for item in snapshot.instruments} == {"equity", "crypto"}
    assert snapshot.backtests
    assert all(item.readiness in {"decision_ready", "research_only", "not_ready"} for item in snapshot.backtests)
    assert all(signal.posture in {"long_research", "short_research", "abstain"} for signal in snapshot.signals)
```

- [ ] **Step 2: Verify red**

Run: `.venv/bin/pytest tests/integration/test_native_snapshot_demo.py -q`
Expected: missing native sections.

- [ ] **Step 3: Wire stages and truth-preserving report copy**

Add explicit `ingest_crypto`, `build_crypto_features`, `train_crypto`, `backtest_crypto`, and `export_native_snapshot` stages. Make run hashes include the relevant instrument/model/backtest configuration. Preserve last-known-good snapshots on failed rebuilds. Update generated reports to separate development and final-test results and to label all non-ready strategies.

- [ ] **Step 4: Rebuild and audit**

Run: `make clean-generated && make demo && .venv/bin/pytest tests/integration/test_demo.py tests/integration/test_native_snapshot_demo.py tests/integration/test_report_pipeline.py -q`
Expected: all stages succeed from clean state; snapshots validate; no source, chronology, or join invariant fails.

- [ ] **Step 5: Commit**

```bash
git add src tests Makefile reports/.gitkeep
git commit -m "feat: orchestrate native research demo"
```

### Task 7: Swift snapshot decoding, repository, and fixture parity

**Files:**
- Create: `macos/Nowcaster/Sources/NowcasterApp/Models/Snapshot.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Services/SnapshotRepository.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Services/AppEnvironment.swift`
- Create: `macos/Nowcaster/Tests/NowcasterAppTests/SnapshotDecodingTests.swift`
- Create: `macos/Nowcaster/Tests/NowcasterAppTests/SnapshotRepositoryTests.swift`
- Create: `macos/Nowcaster/Resources/Fixtures/nowcaster-snapshot.json`

**Interfaces:**
- Produces: `NowcasterSnapshot: Decodable, Sendable`, `SnapshotRepository`, and observable `AppEnvironment` with loading/loaded/stale/incompatible/failure states.

- [ ] **Step 1: Generate the fixture and write failing Swift contract tests**

```swift
@Test func decodesPythonGeneratedFixture() throws {
    let data = try #require(Bundle.module.url(forResource: "nowcaster-snapshot", withExtension: "json", subdirectory: "Fixtures")).map(Data.init(contentsOf:))
    let snapshot = try JSONDecoder.nowcaster.decode(NowcasterSnapshot.self, from: data)
    #expect(snapshot.schemaVersion == 1)
    #expect(snapshot.instruments.contains { $0.assetClass == .crypto })
}

@Test func rejectsUnknownSchema() async {
    await #expect(throws: SnapshotRepositoryError.incompatibleSchema(999)) {
        try await repository.load(data: Data("{\"schema_version\":999}".utf8))
    }
}
```

- [ ] **Step 2: Verify red**

Run: `make macos-test`
Expected: missing Swift model and repository types.

- [ ] **Step 3: Implement exact snake-case decoding and last-known-good state**

Use `JSONDecoder.keyDecodingStrategy = .convertFromSnakeCase`, ISO-8601 dates with fractional-seconds fallback, typed enums with explicit `.unknown(String)` decoding where source values may extend, and `@MainActor @Observable` state. Never replace a loaded snapshot after a decoding failure.

- [ ] **Step 4: Run parity tests**

Run: `.venv/bin/python -m src.cli export-app-snapshot --output macos/Nowcaster/Resources/Fixtures/nowcaster-snapshot.json && make macos-test`
Expected: Python emits a valid fixture and all Swift decoding tests pass.

- [ ] **Step 5: Commit**

```bash
git add macos/Nowcaster src/app_snapshot tests
git commit -m "feat: load research snapshots in Swift"
```

### Task 8: Safe native engine runner and Settings

**Files:**
- Create: `macos/Nowcaster/Sources/NowcasterApp/Services/EngineRunner.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Models/EngineJob.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/Settings/SettingsView.swift`
- Create: `macos/Nowcaster/Tests/NowcasterAppTests/EngineRunnerTests.swift`
- Create: `macos/Nowcaster/Tests/NowcasterAppTests/SettingsTests.swift`

**Interfaces:**
- Produces: `EngineJob`, `EngineProgressEvent`, `EngineRunner.run(_:configuration:) -> AsyncThrowingStream<EngineProgressEvent, Error>`, cancellation, and persisted nonsecret `AppSettings`.

- [ ] **Step 1: Write failing argument-safety and progress tests**

```swift
@Test func engineArgumentsNeverUseAShell() {
    let invocation = EngineJob.fullBacktest.invocation(configuration: fixtureConfiguration)
    #expect(invocation.executableURL.lastPathComponent == "python")
    #expect(invocation.arguments == ["-m", "src.cli", "run-all", "--mode", "demo", "--project-root", fixtureConfiguration.projectRoot.path])
    #expect(!invocation.arguments.contains("sh"))
}

@Test func parsesStructuredProgressLine() throws {
    let event = try EngineProgressEvent.parse("{\"event\":\"stage_started\",\"stage\":\"train\",\"progress\":0.6}")
    #expect(event.stage == "train")
}
```

- [ ] **Step 2: Verify red**

Run: `make macos-test`
Expected: missing runner types.

- [ ] **Step 3: Implement Process runner, cancellation, health check, and Settings scene**

Pass executable and argument arrays directly to `Process`. Read newline-delimited JSON from stdout and bounded plain diagnostics from stderr. Terminate on cancellation and wait for exit. Settings validates project root, Python executable, and snapshot path without storing provider secrets.

- [ ] **Step 4: Run runner tests with a fixture executable**

Run: `make macos-test`
Expected: PASS for progress, nonzero exit, cancellation, invalid path, and last-known-good reload.

- [ ] **Step 5: Commit**

```bash
git add macos/Nowcaster
git commit -m "feat: run research jobs from macOS"
```

### Task 9: HIG-native application shell and shared components

**Files:**
- Create: `macos/Nowcaster/Sources/NowcasterApp/AppModel.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/RootView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Components/ResearchStatusLabel.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Components/MetricSummary.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Components/EmptyStateView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Components/AccessibleChartContainer.swift`
- Create: `macos/Nowcaster/Tests/NowcasterAppTests/AppModelTests.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/NowcasterApp.swift`

**Interfaces:**
- Produces: three-column navigation, global search, toolbar, commands, window restoration, semantic status components, and chart summary/table alternative.

- [ ] **Step 1: Write failing navigation/search tests**

```swift
@Test @MainActor func globalSearchFindsSymbolsAndSelectsMarket() {
    let model = AppModel(snapshot: fixtureSnapshot)
    model.searchText = "ETH"
    #expect(model.searchResults.map(\.symbol) == ["ETH-USD"])
    model.selectSearchResult(model.searchResults[0])
    #expect(model.destination == .markets)
    #expect(model.selectedInstrumentID == "ETH-USD")
}
```

- [ ] **Step 2: Verify red**

Run: `make macos-test`
Expected: missing `AppModel` and shell.

- [ ] **Step 3: Implement system navigation and components**

Use `NavigationSplitView`, `List(selection:)`, `.searchable`, standard `.toolbar`, `Commands`, `Settings`, semantic foreground styles, system accent, and `@SceneStorage`. Add shortcuts for refresh (`⌘R`), search (`⌘F`), backtest (`⇧⌘B`), sidebar (`⌥⌘S`), and export (`⇧⌘E`). No custom window chrome or fixed background palette.

- [ ] **Step 4: Build and test the shell**

Run: `make macos-test && make macos-app`
Expected: PASS and bundle launches into a navigable shell with fixture data.

- [ ] **Step 5: Commit**

```bash
git add macos/Nowcaster
git commit -m "feat: add native macOS navigation shell"
```

### Task 10: Today, Markets, Earnings, and Signals workflows

**Files:**
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/Today/TodayView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/Markets/MarketsView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/Markets/InstrumentDetailView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/Earnings/EarningsView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/Earnings/EarningsDetailView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/Signals/SignalsView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/Signals/SignalDetailView.swift`
- Create: `macos/Nowcaster/Tests/NowcasterAppTests/MonitorViewModelTests.swift`

**Interfaces:**
- Consumes: `AppModel` and snapshot collections.
- Produces: native sortable monitors and evidence-rich detail views for the primary user journey.

- [ ] **Step 1: Write failing sort/filter/evidence tests**

```swift
@Test func signalRankingPlacesEligibleEvidenceBeforeAbstentions() {
    let rows = SignalListModel(signals: fixtureSnapshot.signals).visibleSignals
    #expect(rows.first?.eligibility == .eligible)
    #expect(rows.last?.posture == .abstain)
}

@Test func earningsNeverLabelsProxyAsConsensus() {
    let detail = EarningsDetailModel(forecast: proxyForecast)
    #expect(detail.expectationTitle == "Seasonal expectation proxy")
    #expect(!detail.expectationTitle.contains("Consensus"))
}
```

- [ ] **Step 2: Verify red**

Run: `make macos-test`
Expected: missing feature models/views.

- [ ] **Step 3: Implement native tables, charts, selection, and evidence details**

Use `Table` with sortable columns for Markets, Earnings, and Signals. Use Swift Charts with selection scrubbers and concise chart summaries. Today prioritizes stale/failed sources before signals. Signal details pair posture colors with SF Symbols and text, show calibration status, and place catalyst/invalidation beside the research posture.

- [ ] **Step 4: Test and launch monitor workflows**

Run: `make macos-test && make macos-app && open build/Nowcaster.app`
Expected: all selections, filters, period controls, and detail navigation work with real fixture content.

- [ ] **Step 5: Commit**

```bash
git add macos/Nowcaster
git commit -m "feat: build native monitoring workflows"
```

### Task 11: Backtests, Model Lab, Data Quality, and Pipeline Runs

**Files:**
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/Backtests/BacktestsView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/Backtests/BacktestDetailView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/ModelLab/ModelLabView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/DataQuality/DataQualityView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/PipelineRuns/PipelineRunsView.swift`
- Create: `macos/Nowcaster/Tests/NowcasterAppTests/BacktestPresentationTests.swift`

**Interfaces:**
- Produces: robustness-first backtest and diagnostic workflows with development/final-test separation.

- [ ] **Step 1: Write failing readiness and metric presentation tests**

```swift
@Test func notReadyBacktestDoesNotUsePositiveRecommendationCopy() {
    let model = BacktestDetailModel(backtest: .fixture(readiness: .notReady))
    #expect(model.verdictTitle == "Not decision-ready")
    #expect(!model.summary.localizedCaseInsensitiveContains("profitable strategy"))
}

@Test func finalTestMetricsAreSeparatedFromDevelopment() {
    let model = BacktestDetailModel(backtest: fixtureBacktest)
    #expect(model.developmentMetrics.period != model.finalTestMetrics.period)
}
```

- [ ] **Step 2: Verify red**

Run: `make macos-test`
Expected: missing diagnostic features.

- [ ] **Step 3: Implement charts, tables, assumptions, and run controls**

Backtest detail includes verdict, assumptions, development/final-test metrics, equity curve, drawdown, rolling risk, exposure, turnover, monthly table, fold stability, parameter sensitivity, robustness, and warnings. Model Lab shows horizon/ablation/fold performance and calibration. Data Quality and Pipeline Runs expose provenance and recovery actions without raw-log overload.

- [ ] **Step 4: Test and launch diagnostics**

Run: `make macos-test && make macos-app && open build/Nowcaster.app`
Expected: all diagnostic views render populated and empty/error fixtures without clipping or crashes.

- [ ] **Step 5: Commit**

```bash
git add macos/Nowcaster
git commit -m "feat: add native backtest and diagnostics"
```

### Task 12: Accessibility, appearance, UI automation, and visual QA

**Files:**
- Create: `macos/Nowcaster/UITests/NowcasterUITests.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Accessibility/ChartAccessibility.swift`
- Create: `scripts/capture_macos_app.swift`
- Create: `docs/images/macos/*.png`
- Modify: all primary Swift views where audit finds issues.

**Interfaces:**
- Produces: keyboard/VoiceOver-accessible application and evidence screenshots for every primary screen in light and dark appearances.

- [ ] **Step 1: Add failing accessibility contract tests**

```swift
@Test func everyChartHasSummaryAndTableAlternative() {
    for chart in ChartAccessibility.fixtureCharts {
        #expect(!chart.summary.isEmpty)
        #expect(!chart.rows.isEmpty)
    }
}

@Test func directionDescriptionsDoNotDependOnColor() {
    #expect(ResearchPosture.longResearch.accessibilityDescription.contains("Long research"))
    #expect(ResearchPosture.shortResearch.accessibilityDescription.contains("Short research"))
}
```

- [ ] **Step 2: Verify red**

Run: `make macos-test`
Expected: missing accessibility contracts.

- [ ] **Step 3: Implement accessibility and automation identifiers**

Add chart descriptors and tabular disclosure, contextual labels/hints, logical focus order, keyboard selection, reduced-motion guards, increased-contrast borders where needed, and stable identifiers. Automate navigation and screenshot capture using XCTest/AppKit APIs without a browser.

- [ ] **Step 4: Perform visual and accessibility QA**

Run: `make macos-app && make macos-ui-test && make macos-screenshots`
Inspect every screenshot at 1,440×900 and a narrow 1,080×720 window in light/dark appearance. Run Accessibility Inspector audit. Fix clipping, density, contrast, focus, semantic-copy, and chart issues, then recapture.

- [ ] **Step 5: Commit**

```bash
git add macos/Nowcaster scripts/capture_macos_app.swift docs/images/macos
git commit -m "test: verify macOS accessibility and visuals"
```

### Task 13: Packaging, CI, documentation, and release readiness

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `.github/workflows/release.yml`
- Create: `docs/macos_app.md`
- Create: `docs/backtest_protocol.md`
- Create: `docs/privacy.md`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/methodology.md`
- Modify: `.gitignore`
- Modify: `Makefile`

**Interfaces:**
- Produces: primary native-product documentation, deterministic CI, zipped app artifact, checksums, and optional signing/notarization path.

- [ ] **Step 1: Write failing documentation and workflow tests**

```python
def test_readme_is_native_first_and_documents_no_web_runtime():
    text = Path("README.md").read_text()
    assert "make macos-app" in text
    assert "SwiftUI" in text
    assert "WebView" in text
    assert "not investment advice" in text.lower()

def test_ci_runs_both_language_suites():
    workflow = yaml.safe_load(Path(".github/workflows/ci.yml").read_text())
    rendered = json.dumps(workflow)
    assert "pytest" in rendered and "swift test" in rendered and "make demo" in rendered
```

- [ ] **Step 2: Verify red**

Run: `.venv/bin/pytest tests/unit/test_documentation.py -q`
Expected: native docs/workflow assertions fail.

- [ ] **Step 3: Implement CI, release workflow, and native-first documentation**

CI uses a pinned Python range and a current macOS runner, installs from the public PyPI index, runs Ruff, pytest with coverage, clean demo, snapshot validation, Swift tests, and app assembly. Release zips `Nowcaster.app`, produces SHA-256, uploads artifacts, and conditionally signs/notarizes only when secrets exist. README leads with the native app and measured evidence; Streamlit is documented only as a deprecated research fallback or removed.

- [ ] **Step 4: Validate workflows and documentation**

Run: `.venv/bin/pytest tests/unit/test_documentation.py -q && make lint && make macos-test && make macos-app`
Expected: PASS and generated app matches documented commands.

- [ ] **Step 5: Commit**

```bash
git add .github README.md docs Makefile .gitignore tests/unit/test_documentation.py
git commit -m "docs: ship native macOS nowcaster"
```

### Task 14: Clean rebuild, full audit, GitHub publication

**Files:**
- Create: `docs/native_verification.md`
- Modify: any file that fails final verification.

**Interfaces:**
- Produces: authoritative verification evidence, clean `main`, and complete remote repository at `https://github.com/james8464/nowcaster.git`.

- [ ] **Step 1: Run all static and automated checks from a clean generated state**

Run:

```bash
make clean-generated
make lint
.venv/bin/pytest --cov=src --cov-report=term-missing -q
make demo
make macos-test
make macos-app
make macos-ui-test
make dashboard-smoke
```

Expected: every command exits zero; Python tests, Swift tests, integration/UI tests, engine snapshot, app bundle, and legacy migration smoke are green.

- [ ] **Step 2: Audit evidence and invariants**

Verify snapshot hashes, natural-key uniqueness, feature chronology, label availability, purged/embargoed folds, final-test isolation, execution lag, transaction costs, signal/backtest joins, schema parity, source labels, report claims, app copy, and generated checksums. Record exact row counts, model observations, trade counts, readiness, and measured final-test metrics without selecting only favorable results.

- [ ] **Step 3: Launch and inspect the release app**

Run: `open build/Nowcaster.app` and exercise all primary workflows, refresh progress, failure recovery, keyboard commands, Settings, light/dark appearance, resize behavior, and accessibility. Re-run `codesign --verify --deep --strict build/Nowcaster.app` and `spctl --assess --type execute --verbose build/Nowcaster.app` while documenting that ad-hoc builds are not notarized distributions.

- [ ] **Step 4: Write verification record and commit fixes**

```bash
git add docs/native_verification.md .
git commit -m "test: verify native nowcaster release"
```

- [ ] **Step 5: Integrate and publish**

Fast-forward the verified feature branch into `main`, ensure `git status --short` is empty, then:

```bash
git remote add origin https://github.com/james8464/nowcaster.git
git push -u origin main
gh repo view james8464/nowcaster --json url,defaultBranchRef
git ls-remote --heads origin main
```

If `origin` already exists, verify its normalized URL instead of replacing it. Confirm the remote `main` object ID equals local `HEAD`, and record the repository URL and commit in `docs/native_verification.md` before the final push if that file needs the hash.
