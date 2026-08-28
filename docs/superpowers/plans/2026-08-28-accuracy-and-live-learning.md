# Accuracy and Live Learning Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Nowcaster consume richer point-in-time data, predict execution-aware target-before-stop outcomes, calibrate and abstain conservatively, govern self-improvement, detect drift, preserve live compute, and explain its evidence in the native macOS app.

**Architecture:** Extend existing immutable provider and database contracts additively, then add pure outcome/calibration/drift modules that feed the current validation and live-evidence boundaries. Keep the Python engine as the sole quantitative implementation and expose optional evidence fields through the existing snapshot to SwiftUI.

**Tech Stack:** Python 3.13, Pydantic 2, pandas, NumPy, SciPy, scikit-learn, SQLAlchemy/DuckDB, pytest, Swift 6, SwiftUI, Swift Testing.

**Spec:** `docs/superpowers/specs/2026-08-28-accuracy-and-live-learning-design.md`

## Global Constraints

- Finalized, point-in-time observations only; future appends must not change earlier decisions.
- Provider/feed/venue/product identities never splice silently.
- New database and snapshot fields are additive and backward-compatible.
- Missing entitlements, calibration, data, or forward evidence fail closed.
- Generated challengers never deploy themselves and cannot submit broker orders.
- Research reserves at least two logical processors for live/UI work.
- No new external dependency is required for the deterministic test suite.

---

### Task 1: Rich historical bar contract and additive storage migration

**Files:**
- Modify: `src/ingestion/bars.py`
- Modify: `src/ingestion/binance_bars.py`
- Modify: `src/database/schema.py`
- Modify: `src/database/engine.py`
- Modify: `src/strategies/pipeline.py`
- Test: `tests/unit/test_bar_ingestion.py`
- Test: `tests/unit/test_database.py`
- Test: `tests/integration/test_bar_store.py`

**Interfaces:**
- Produces: nullable `MarketBar.quote_volume`, `taker_buy_base_volume`, and `taker_buy_quote_volume`.
- Produces: schema version 7 with the same nullable columns on `market_bars`.

- [ ] **Step 1: Write failing provider and storage tests**

```python
def test_binance_retains_documented_flow_fields(binance_provider):
    bar = list(binance_provider.fetch(request()))[0]
    assert bar.quote_volume == 1413503.917251
    assert bar.taker_buy_base_volume == 498.0
    assert bar.taker_buy_quote_volume == 744750.0
```

- [ ] **Step 2: Run the focused tests and verify missing-field failures**

Run: `.venv/bin/pytest -q tests/unit/test_bar_ingestion.py tests/unit/test_database.py tests/integration/test_bar_store.py`

- [ ] **Step 3: Implement nullable fields, Binance mapping, migrations, and persistence projection**

```python
class MarketBar(BaseModel):
    quote_volume: float | None = Field(default=None, ge=0)
    taker_buy_base_volume: float | None = Field(default=None, ge=0)
    taker_buy_quote_volume: float | None = Field(default=None, ge=0)
```

- [ ] **Step 4: Run the focused tests green and commit**

Run: `.venv/bin/pytest -q tests/unit/test_bar_ingestion.py tests/unit/test_database.py tests/integration/test_bar_store.py`

Commit: `feat: retain Binance order-flow bar fields`

### Task 2: Rich live quote, trade, depth, status, and correction events

**Files:**
- Modify: `src/live_monitor/types.py`
- Modify: `src/live_monitor/providers.py`
- Modify: `src/live_monitor/repository.py`
- Modify: `src/database/schema.py`
- Modify: `src/database/engine.py`
- Modify: `tests/fixtures/live_monitor/alpaca_stream.jsonl`
- Modify: `tests/fixtures/live_monitor/binance_stream.jsonl`
- Test: `tests/unit/test_live_monitor_types.py`
- Test: `tests/unit/test_live_monitor_providers.py`
- Test: `tests/integration/test_live_monitor_repository.py`

**Interfaces:**
- Produces: `MarketQuote.bid_size`, `ask_size`, `sequence`, and `processed_at`.
- Produces: `MarketTrade`, `MarketDepth`, and `MarketStatusEvent` union members.
- Produces: append-only `live_market_events` persistence through `record_market_event`.

- [ ] **Step 1: Write failing decoder, validation, and round-trip tests**

```python
def test_binance_decodes_sizes_and_aggregate_trade(adapter):
    quote, trade = decode_fixture(adapter)
    assert (quote.bid_size, quote.ask_size) == (Decimal("1.25"), Decimal("0.75"))
    assert trade.aggressor == "sell"
    assert trade.sequence > 0
```

- [ ] **Step 2: Verify the tests fail because the event contracts do not exist**

Run: `.venv/bin/pytest -q tests/unit/test_live_monitor_types.py tests/unit/test_live_monitor_providers.py tests/integration/test_live_monitor_repository.py`

- [ ] **Step 3: Implement strict event types and provider subscriptions**

Alpaca subscription includes `trades`, `quotes`, `bars`, `statuses`, `lulds`, `corrections`, and `cancelErrors`. Binance subscription includes `aggTrade`, `bookTicker`, `depth@100ms`, and `kline_1m`. Unknown but valid provider control messages are ignored; malformed market events raise `ProviderDecodeError`.

- [ ] **Step 4: Persist normalized events with bounded payloads and sequence identity**

```python
def record_market_event(self, session_id: str, event: MarketEvent) -> bool:
    """Append an immutable normalized event; duplicate event ids return False."""
```

- [ ] **Step 5: Run focused tests green and commit**

Run: `.venv/bin/pytest -q tests/unit/test_live_monitor_types.py tests/unit/test_live_monitor_providers.py tests/integration/test_live_monitor_repository.py`

Commit: `feat: capture granular live market events`

### Task 3: Point-in-time barrier outcomes and empirical level support

**Files:**
- Create: `src/models/trade_outcomes.py`
- Modify: `src/models/__init__.py`
- Modify: `src/live_monitor/levels.py`
- Modify: `src/live_monitor/engine.py`
- Test: `tests/unit/test_trade_outcomes.py`
- Modify: `tests/unit/test_live_monitor_levels.py`
- Modify: `tests/unit/test_live_monitor_engine.py`

**Interfaces:**
- Produces: `BarrierPolicy`, `TradeOutcome`, `label_trade_outcomes(frame, policy)`.
- Produces: `EmpiricalLevelEvidence` and `select_empirical_levels(...)`.

- [ ] **Step 1: Write failing causal outcome tests**

```python
def test_same_bar_stop_and_target_uses_adverse_ordering():
    result = label_trade_outcomes(bars, BarrierPolicy(target_r=1, stop_r=1, maximum_bars=3))
    assert result.iloc[0].exit_reason == "ambiguous_stop_first"
    assert result.iloc[0].net_return < 0
    assert result.iloc[0].outcome_available_at == bars.iloc[1].available_at
```

- [ ] **Step 2: Run and observe missing-module failure**

Run: `.venv/bin/pytest -q tests/unit/test_trade_outcomes.py`

- [ ] **Step 3: Implement long/short first-passage labels, MAE/MFE, duration, costs, and causality checks**

- [ ] **Step 4: Run outcome tests green**

Run: `.venv/bin/pytest -q tests/unit/test_trade_outcomes.py`

- [ ] **Step 5: Write failing empirical-level tests**

The tests require development-only samples, minimum effective sample size, positive lower expected-net-edge bound, ordered rounded levels, and abstention when support is insufficient.

- [ ] **Step 6: Replace static-only ATR target selection with empirical support plus safe fallback abstention**

```python
def select_empirical_levels(
    outcomes: Sequence[TradeOutcome], quote: MarketQuote, direction: Direction, policy: TradeLevelPolicy
) -> EmpiricalLevelEvidence | None: ...
```

- [ ] **Step 7: Run live level/engine tests green and commit**

Run: `.venv/bin/pytest -q tests/unit/test_trade_outcomes.py tests/unit/test_live_monitor_levels.py tests/unit/test_live_monitor_engine.py`

Commit: `feat: align signals with target-before-stop outcomes`

### Task 4: Calibration diagnostics, conservative intervals, and selective thresholds

**Files:**
- Rewrite: `src/models/calibration.py`
- Modify: `src/models/metrics.py`
- Modify: `src/strategies/validation.py`
- Modify: `src/strategies/pipeline.py`
- Modify: `src/live_monitor/evidence.py`
- Test: `tests/unit/test_model_calibration.py`
- Modify: `tests/unit/test_model_metrics.py`
- Modify: `tests/unit/test_strategy_validation.py`
- Modify: `tests/unit/test_live_monitor_evidence.py`

**Interfaces:**
- Produces: `CalibrationReport` with sample/effective sample, Brier, log loss, ECE, confidence bounds, and slice identity.
- Produces: `fit_out_of_fold_calibration(probabilities, outcomes, timestamps, method="auto")`.
- Produces: `selective_threshold(report_rows, minimum_coverage, confidence)`.
- Live cohort accepts `oof_beta_v2`, `oof_sigmoid_v2`, or `oof_isotonic_v2` only.

- [ ] **Step 1: Write failing literal calibration tests**

```python
def test_calibration_report_uses_hand_checked_brier_and_effective_sample():
    report = calibration_report(np.array([0.25, 0.75]), np.array([0, 1]))
    assert report.brier_score == pytest.approx(0.0625)
    assert report.sample_size == 2
    assert 0 < report.effective_sample_size <= 2
```

- [ ] **Step 2: Verify focused tests fail for missing APIs**

Run: `.venv/bin/pytest -q tests/unit/test_model_calibration.py tests/unit/test_model_metrics.py`

- [ ] **Step 3: Implement robust clipping, Brier/log-loss/ECE, autocorrelation-adjusted sample size, Wilson/Beta interval, and out-of-fold calibrators**

- [ ] **Step 4: Write failing live evidence tests for minimum effective sample and lower-bound edge**

- [ ] **Step 5: Version and validate the richer sealed calibration evidence; remove the five-observation promotable path**

- [ ] **Step 6: Run all calibration/evidence tests green and commit**

Run: `.venv/bin/pytest -q tests/unit/test_model_calibration.py tests/unit/test_model_metrics.py tests/unit/test_strategy_validation.py tests/unit/test_live_monitor_evidence.py`

Commit: `feat: calibrate selective live probabilities`

### Task 5: Named validation tiers, rolling sealed holdouts, and global trial control

**Files:**
- Modify: `src/strategies/validation.py`
- Modify: `src/backtest/robustness.py`
- Modify: `src/deep_research/promotion.py`
- Modify: `src/deep_research/contracts.py`
- Modify: `src/deep_research/repository.py`
- Modify: `src/learning/search.py`
- Test: `tests/unit/test_strategy_validation.py`
- Modify: `tests/unit/test_backtest_robustness.py`
- Modify: `tests/unit/test_deep_research_promotion.py`
- Modify: `tests/integration/test_deep_research_repository.py`
- Modify: `tests/unit/test_learning_search.py`

**Interfaces:**
- Produces: `ValidationTier` and `ValidationConfig.for_tier(tier)`.
- Produces: `make_rolling_sealed_boundaries(...)`.
- Produces: `effective_sample_size(returns)` and `lower_mean_confidence_bound(returns, confidence)`.
- Trial uniqueness includes search family, dataset, protocol, semantic candidate hash, and attempt ordinal.

- [ ] **Step 1: Write failing tier and rolling-boundary tests**

```python
def test_exploratory_tier_can_never_promote():
    config = ValidationConfig.for_tier(ValidationTier.EXPLORATORY)
    assert "exploratory evidence cannot promote" in promotion_reasons(perfect_inputs(), config)
```

- [ ] **Step 2: Verify failure, then implement immutable named policies and rolling sealed boundaries**

- [ ] **Step 3: Write failing effective-sample, confidence-bound, and global-trial tests**

- [ ] **Step 4: Implement autocorrelation-aware evidence and persistent global trial identity**

- [ ] **Step 5: Run validation/search suites green and commit**

Run: `.venv/bin/pytest -q tests/unit/test_strategy_validation.py tests/unit/test_backtest_robustness.py tests/unit/test_deep_research_promotion.py tests/integration/test_deep_research_repository.py tests/unit/test_learning_search.py`

Commit: `feat: enforce promotion-grade validation tiers`

### Task 6: Observed execution-cost model and paper calibration ledger

**Files:**
- Modify: `src/backtest/execution.py`
- Modify: `src/backtest/costs.py`
- Modify: `src/trading/types.py`
- Modify: `src/trading/forward.py`
- Modify: `src/database/schema.py`
- Modify: `src/database/engine.py`
- Test: `tests/unit/test_execution_engine.py`
- Modify: `tests/unit/test_forward_evidence.py`
- Modify: `tests/integration/test_trading_repository.py`

**Interfaces:**
- Produces: quote-aware `ExecutionObservation` with predicted/realized spread, slippage, latency, fill fraction, funding, borrow, and impact.
- Produces: `execution_model_error(observations)` and confidence interval.

- [ ] **Step 1: Write failing execution observation and conservative-cost tests**

- [ ] **Step 2: Run focused tests and confirm missing behavior**

Run: `.venv/bin/pytest -q tests/unit/test_execution_engine.py tests/unit/test_forward_evidence.py tests/integration/test_trading_repository.py`

- [ ] **Step 3: Implement observed cost fields, storage, and fail-closed comparison against the predicted simulator**

- [ ] **Step 4: Run focused tests green and commit**

Commit: `feat: reconcile simulated and observed execution`

### Task 7: Live drift detection and readiness invalidation

**Files:**
- Create: `src/models/drift.py`
- Modify: `src/live_monitor/evidence.py`
- Modify: `src/live_monitor/engine.py`
- Modify: `src/trading/live_monitor_readiness.py`
- Test: `tests/unit/test_model_drift.py`
- Modify: `tests/unit/test_live_monitor_evidence.py`
- Modify: `tests/unit/test_live_monitor_engine.py`
- Modify: `tests/unit/test_live_readiness.py`

**Interfaces:**
- Produces: `AdaptiveMeanDrift`, `DriftMetric`, `DriftReport`, and `assess_drift(...)`.
- Readiness receipts bind a drift-policy hash and become invalid on confirmed material drift.

- [ ] **Step 1: Write failing stationary/change-point/invalid-input tests**

```python
def test_adaptive_monitor_detects_persistent_mean_shift():
    monitor = AdaptiveMeanDrift(minimum_window=20, confidence=0.99)
    reports = [monitor.update(value) for value in ([0.0] * 40 + [2.0] * 40)]
    assert any(report.confirmed for report in reports[-20:])
```

- [ ] **Step 2: Verify failure and implement deterministic bounded drift monitoring**

- [ ] **Step 3: Write and implement fail-closed live/readiness integration tests**

- [ ] **Step 4: Run focused tests green and commit**

Run: `.venv/bin/pytest -q tests/unit/test_model_drift.py tests/unit/test_live_monitor_evidence.py tests/unit/test_live_monitor_engine.py tests/unit/test_live_readiness.py`

Commit: `feat: invalidate stale models on live drift`

### Task 8: Resource-safe champion/challenger governance

**Files:**
- Modify: `src/deep_research/coordinator.py`
- Modify: `src/deep_research/contracts.py`
- Modify: `src/deep_research/repository.py`
- Modify: `src/learning/promotion.py`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Services/DeepResearchControl.swift`
- Test: `tests/integration/test_deep_research_coordinator.py`
- Modify: `tests/unit/test_deep_research_contracts.py`
- Modify: `tests/unit/test_learning_search.py`
- Modify: `macos/Nowcaster/Tests/NowcasterAppTests/StrategyLabTests.swift`

**Interfaces:**
- Produces: `recommended_worker_count(active_processors, reserved_processors=2, configured_max=None)`.
- Promotion records explicit incumbent, challenger, shadow cohort, rollback target, and evidence reset.

- [ ] **Step 1: Write failing worker-reservation and cohort-reset tests**

- [ ] **Step 2: Implement worker reservation and resource-preemption reasons**

- [ ] **Step 3: Implement immutable challenger → shadow → forward-qualified state transitions and rollback records**

- [ ] **Step 4: Run Python and Swift focused tests green and commit**

Run: `.venv/bin/pytest -q tests/integration/test_deep_research_coordinator.py tests/unit/test_deep_research_contracts.py tests/unit/test_learning_search.py`

Run: `cd macos/Nowcaster && swift test --filter StrategyLab`

Commit: `feat: govern resource-safe strategy improvement`

### Task 9: Accuracy evidence in snapshots and SwiftUI

**Files:**
- Modify: `src/app_snapshot/models.py`
- Modify: `src/app_snapshot/builder.py`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Models/Snapshot.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Components/ResearchFormatting.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Features/Signals/SignalDetailView.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Features/Today/TodayView.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Features/LiveMonitor/LiveMonitorView.swift`
- Test: `tests/unit/test_app_snapshot.py`
- Modify: `macos/Nowcaster/Tests/NowcasterAppTests/SnapshotDecodingTests.swift`
- Modify: `macos/Nowcaster/Tests/NowcasterAppTests/AccessibilityContractTests.swift`
- Modify: `macos/Nowcaster/Tests/NowcasterAppTests/LiveMonitorPresentationTests.swift`

**Interfaces:**
- `ResearchSignalSnapshot` gains optional provider/feed/venue/product, probability definition/range, calibration sample/effective sample, Brier/ECE, gross edge/cost/lower net edge, model age, regime, drift, latency, and coverage fields.
- Older snapshots continue decoding with nil evidence.

- [ ] **Step 1: Write failing Python and Swift backward-compatibility/presentation tests**

- [ ] **Step 2: Add optional snapshot fields and builder projections**

- [ ] **Step 3: Build native disclosure groups and accessible labels using semantic SwiftUI styles and SF Symbols**

- [ ] **Step 4: Run focused Python and Swift tests green and commit**

Run: `.venv/bin/pytest -q tests/unit/test_app_snapshot.py tests/integration/test_app_snapshot_export.py`

Run: `cd macos/Nowcaster && swift test --filter SnapshotDecoding && swift test --filter Accessibility && swift test --filter LiveMonitorPresentation`

Commit: `feat: explain calibrated accuracy in macOS`

### Task 10: Correct instrument configuration and beginner documentation

**Files:**
- Modify: `config/instruments.yaml`
- Modify: `config/deep_research.yaml`
- Modify: `README.md`
- Modify: `docs/methodology.md`
- Modify: `docs/backtest_protocol.md`
- Modify: `docs/live-readiness.md`
- Modify: `docs/live-monitor.md`
- Modify: `docs/data-providers.md`
- Modify: `docs/data_dictionary.md`
- Test: `tests/unit/test_config.py`
- Modify: `tests/unit/test_documentation.py`

**Interfaces:**
- Bundled instruments identify Binance USDT spot exactly and declare non-shortable spot semantics.
- Resource settings reserve two processors and bound calibration/promotion requirements.

- [ ] **Step 1: Write failing behavior tests for configuration identity and rendered documentation contracts**

- [ ] **Step 2: Update configuration and beginner-readable operational documentation**

- [ ] **Step 3: Run focused tests green and commit**

Run: `.venv/bin/pytest -q tests/unit/test_config.py tests/unit/test_documentation.py`

Commit: `docs: document calibrated learning and data limits`

### Task 11: Full regression, artifact synchronization, release, and remote publication

**Files:**
- Modify generated fixture only through: `scripts/synchronize_snapshot_fixture.py`
- Modify generated screenshots only through: `scripts/capture_macos_app.swift`
- Modify: `docs/research-results.md` only if a fresh provider-backed run produces new evidence

**Interfaces:**
- Produces a clean, reproducible, pushed `main` commit; external provider absence remains explicit.

- [ ] **Step 1: Format and lint**

Run: `.venv/bin/ruff format . && .venv/bin/ruff check .`

- [ ] **Step 2: Run complete Python and Swift suites**

Run: `.venv/bin/pytest -q`

Run: `cd macos/Nowcaster && swift test`

- [ ] **Step 3: Synchronize fixtures and verify deterministic parity**

Run: `make research-ci sync-macos-snapshot verify-research-fixtures verify-swift-fixture-parity`

- [ ] **Step 4: Build and verify live/release artifacts**

Run: `make verify-live-monitor secret-scan engine-bundle macos-app`

Run: `./scripts/verify_production_release.sh build/Nowcaster.app`

- [ ] **Step 5: Inspect the full diff, run `git diff --check`, commit any generated parity changes, and push `main` to `origin`**

Run: `git status --short && git diff --check && git log -1 --oneline`

Run: `git push origin main`
