# Intraday Strategy Research and Learning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a causal intraday strategy research engine, evidence-weighted ensemble, interpretable learning mode, and native SwiftUI Strategy Lab.

**Architecture:** Preserve the existing earnings/daily research path and add parallel timestamped intraday infrastructure. A provider-neutral immutable bar store feeds a versioned causal strategy registry; the same event-driven execution code supports backtest and paper modes, while nested walk-forward validation and a sealed final test control strategy selection and learning. Snapshot schema v2 transports the results into the existing macOS design system.

**Tech Stack:** Python 3.11–3.13, pandas, NumPy, SciPy, scikit-learn, statsmodels, DuckDB/SQLAlchemy, httpx, Pydantic, Typer, Swift 6, SwiftUI, Swift Charts, XCTest.

**Spec:** `docs/superpowers/specs/2026-08-22-intraday-strategy-learning-design.md`

## Global Constraints

- Decisions may read only finalized records with `available_at <= decision_timestamp`.
- Signals derived from bar `t` execute no earlier than the next actionable bar.
- Every registered strategy must pass prefix-invariance tests.
- The final test boundary is selected from the full chronology before signal filtering and is sealed from training, calibration, weighting, learning, and promotion.
- Every adaptive candidate counts in the append-only trial ledger.
- Backtests include fees, spread, slippage, latency, funding/borrow, and conservative intrabar ordering.
- Ensemble weights are nonnegative, shrink toward equal weight, and are capped by strategy and family.
- Missing provider history is disclosed as unavailable, never treated as a successful backtest.
- User-facing copy describes research evidence and uncertainty; it never promises profit.
- The existing native SwiftUI design language, semantic colors, SF Symbols, accessibility, and macOS HIG patterns remain authoritative.
- Raw credentials and bulk licensed market data must never enter Git.

---

### Task 1: Configuration, contracts, and timestamped persistence

**Files:**
- Create: `src/strategies/__init__.py`
- Create: `src/strategies/types.py`
- Create: `src/strategies/registry.py`
- Create: `config/strategies.yaml`
- Modify: `src/config/settings.py`
- Modify: `src/database/schema.py`
- Modify: `src/database/engine.py`
- Test: `tests/unit/test_strategy_registry.py`
- Test: `tests/integration/test_strategy_schema.py`

**Interfaces:**
- Produces: `BarInterval`, `StrategyFamily`, `StrategyMode`, `StrategySpec`, `StrategyRegistry`, and timestamped SQLAlchemy tables.
- Consumes: existing strict Pydantic configuration and `Database` initialization patterns.

```python
class StrategySpec(BaseModel):
    strategy_id: str
    family: StrategyFamily
    version: str
    intervals: tuple[BarInterval, ...]
    warmup_bars: int
    parameters: dict[str, float | int | str | bool]

class StrategyRegistry:
    def register(self, spec: StrategySpec, generator: SignalGenerator) -> None: ...
    def resolve(self, strategy_id: str) -> RegisteredStrategy: ...
    def enabled(self) -> tuple[RegisteredStrategy, ...]: ...
```

- [ ] **Step 1: Write failing registry tests** asserting duplicate IDs fail, invalid intervals/parameters fail, deterministic versions remain stable, enabled YAML strategies load, and family/strategy caps validate.
- [ ] **Step 2: Run** `pytest tests/unit/test_strategy_registry.py -v` and confirm failure because `src.strategies` does not exist.
- [ ] **Step 3: Implement immutable typed contracts** with explicit UTC timestamps, stable canonical JSON hashing, validated registry lookup, and no dynamic import of arbitrary YAML strings.
- [ ] **Step 4: Run the registry tests** and confirm they pass.
- [ ] **Step 5: Write failing schema tests** that initialize a fresh and legacy DuckDB database and assert tables for `market_bars`, `strategy_runs`, `strategy_signals`, `ensemble_weights`, `strategy_executions`, `learning_trials`, `discovered_rules`, and `causal_audits` exist with timestamped natural keys.
- [ ] **Step 6: Run** `pytest tests/integration/test_strategy_schema.py -v` and confirm the new tables are missing.
- [ ] **Step 7: Add parallel timestamped tables and an idempotent schema-version migration** without changing legacy daily keys.
- [ ] **Step 8: Run both task test files**, then `ruff check src/strategies src/config/settings.py src/database`.
- [ ] **Step 9: Commit** with `feat: add strategy contracts and intraday schema`.

### Task 2: Immutable intraday ingestion and dataset manifests

**Files:**
- Create: `src/ingestion/bars.py`
- Create: `src/ingestion/binance_bars.py`
- Create: `src/ingestion/alpaca_bars.py`
- Create: `src/ingestion/csv_bars.py`
- Create: `src/strategies/datasets.py`
- Test: `tests/unit/test_bar_ingestion.py`
- Test: `tests/integration/test_bar_store.py`

**Interfaces:**
- Consumes: `BarInterval` and `market_bars` from Task 1.
- Produces: `MarketBar`, `BarProvider`, `BinanceBarProvider`, `AlpacaBarProvider`, `CSVBarProvider`, `BarRepository`, and `DatasetManifest`.

```python
class BarProvider(Protocol):
    def fetch(self, request: BarRequest) -> Iterable[MarketBar]: ...

class BarRepository:
    def append(self, bars: Iterable[MarketBar]) -> int: ...
    def bars_as_of(self, request: BarQuery, decision_timestamp: datetime) -> pd.DataFrame: ...
    def manifest(self, request: BarQuery) -> DatasetManifest: ...
```

- [ ] **Step 1: Write failing parser tests** using complete Binance/Alpaca response fixtures to prove UTC normalization, finalized-bar rejection, feed identity, paging boundaries, duplicate handling, revision append, and payload hashing.
- [ ] **Step 2: Run** `pytest tests/unit/test_bar_ingestion.py -v` and verify missing imports fail.
- [ ] **Step 3: Implement provider-neutral models and providers** using injected `httpx.Client`, bounded retries, page cursors, rate-limit response handling, and environment-variable credentials that are never logged.
- [ ] **Step 4: Run parser tests** and confirm pass.
- [ ] **Step 5: Write failing repository tests** that insert two revisions of a bar and assert `bars_as_of(decision_timestamp)` returns only the revision available then; assert dataset hashes change with source data and are stable across identical loads.
- [ ] **Step 6: Run** `pytest tests/integration/test_bar_store.py -v` and verify failure.
- [ ] **Step 7: Implement append-only persistence, as-of resolution, coverage discovery, gap reports, checksum manifests, and atomic local cache writes.**
- [ ] **Step 8: Run both task tests and Ruff.**
- [ ] **Step 9: Commit** with `feat: ingest immutable intraday market bars`.

### Task 3: Causal indicators and evidence-backed strategy library

**Files:**
- Create: `src/strategies/indicators.py`
- Create: `src/strategies/library.py`
- Create: `src/strategies/session.py`
- Create: `src/strategies/pairs.py`
- Modify: `src/strategies/registry.py`
- Test: `tests/unit/test_indicators.py`
- Test: `tests/unit/test_strategy_library.py`
- Test: `tests/unit/test_strategy_no_repaint.py`

**Interfaces:**
- Consumes: ordered finalized `MarketBar` frames and `StrategySpec`.
- Produces: `IndicatorFrame`, `StrategySignalFrame`, `build_indicators(frame, session)`, and `generate_signals(spec, bars, context)`.

```python
def build_indicators(bars: pd.DataFrame, session: SessionCalendar) -> pd.DataFrame: ...
def generate_signals(
    spec: StrategySpec,
    bars: pd.DataFrame,
    context: StrategyContext,
) -> pd.DataFrame: ...  # decision_timestamp, data_through, signal, strength, reason
```

- [ ] **Step 1: Write failing indicator tests** with hand-calculated EMA, RSI, ATR, ADX, MACD, stochastic, Bollinger/Keltner, Donchian, VWAP, relative volume, z-score, and session-window expectations.
- [ ] **Step 2: Run** `pytest tests/unit/test_indicators.py -v` and verify missing module failure.
- [ ] **Step 3: Implement trailing-only indicators** with explicit warm-up validity and no backward fill.
- [ ] **Step 4: Run indicator tests** and confirm pass.
- [ ] **Step 5: Write failing table-driven strategy tests** for each configured trend, mean-reversion, volatility/volume, session, pairs, and cross-sectional rule; each fixture has literal expected `-1/0/+1` decisions.
- [ ] **Step 6: Run** `pytest tests/unit/test_strategy_library.py -v` and verify failure.
- [ ] **Step 7: Implement the registered strategy functions** and metadata, including plain-language rule descriptions and research-only flags.
- [ ] **Step 8: Write and run registry-wide prefix tests** that compare every historical signal before and after future bars/corrections are appended; confirm the test initially catches an intentionally future-dependent fixture, then keep only causal implementations.
- [ ] **Step 9: Run all three task test files and Ruff.**
- [ ] **Step 10: Commit** with `feat: add causal intraday strategy library`.

### Task 4: Event-driven execution and realistic portfolio backtesting

**Files:**
- Create: `src/backtest/execution.py`
- Create: `src/backtest/intraday.py`
- Create: `src/backtest/costs.py`
- Modify: `src/backtest/metrics.py`
- Modify: `src/backtest/robustness.py`
- Test: `tests/unit/test_execution_engine.py`
- Test: `tests/unit/test_intraday_backtest.py`
- Test: `tests/unit/test_strategy_statistics.py`

**Interfaces:**
- Consumes: `StrategySignalFrame`, `MarketBar`, execution/cost configuration.
- Produces: `OrderIntent`, `Fill`, `ExecutionAssumptions`, `IntradayBacktestResult`, `run_intraday_backtest`, and robust metric records.

```python
def run_intraday_backtest(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    assumptions: ExecutionAssumptions,
    risk: RiskLimits,
) -> IntradayBacktestResult: ...
```

- [ ] **Step 1: Write failing execution tests** proving next-bar timing, tick/lot rounding, bid/ask direction, fees, spread, slippage, latency, partial participation, funding/borrow, session flattening, and adverse stop/target collision ordering.
- [ ] **Step 2: Run** `pytest tests/unit/test_execution_engine.py -v` and verify missing module failure.
- [ ] **Step 3: Implement the event-driven order/fill state machine** with deterministic ordering and explicit rejection reasons.
- [ ] **Step 4: Run execution tests** and confirm pass.
- [ ] **Step 5: Write failing portfolio tests** for cash/exposure limits, multi-strategy netting, volatility targeting based only on prior returns, and deterministic equity/trade ledgers.
- [ ] **Step 6: Run** `pytest tests/unit/test_intraday_backtest.py -v` and verify failure.
- [ ] **Step 7: Implement the timestamped portfolio simulator and metrics.**
- [ ] **Step 8: Write failing robustness tests** for block bootstrap intervals, Deflated Sharpe, PBO/CSCV, parameter stability, fold/year/side attribution, and doubled-cost survival using hand-checkable fixtures.
- [ ] **Step 9: Implement statistics and run all task tests plus Ruff.**
- [ ] **Step 10: Commit** with `feat: add realistic intraday backtesting`.

### Task 5: Sealed validation, evidence weighting, and current signals

**Files:**
- Create: `src/strategies/validation.py`
- Create: `src/strategies/ensemble.py`
- Create: `src/strategies/engine.py`
- Test: `tests/unit/test_strategy_validation.py`
- Test: `tests/unit/test_strategy_ensemble.py`
- Test: `tests/integration/test_strategy_engine.py`

**Interfaces:**
- Consumes: registered strategies and intraday backtest results.
- Produces: `StrategyEvaluation`, `PromotionDecision`, `EvidenceWeight`, `EnsembleDecision`, `evaluate_registry`, and `generate_current_decision`.

```python
def evaluate_registry(request: EvaluationRequest) -> tuple[StrategyEvaluation, ...]: ...
def generate_current_decision(
    evaluations: Sequence[StrategyEvaluation],
    resolved_outcomes: pd.DataFrame,
    as_of: datetime,
) -> EnsembleDecision: ...
```

- [ ] **Step 1: Write failing validation tests** proving chronological outer folds, label purging, embargo, chronology-based final boundary, and frozen predictions invariant to final labels.
- [ ] **Step 2: Run** `pytest tests/unit/test_strategy_validation.py -v` and verify failure.
- [ ] **Step 3: Implement nested walk-forward/frozen protocols** with development-only fitting and explicit unavailable/failed states.
- [ ] **Step 4: Run validation tests** and confirm pass.
- [ ] **Step 5: Write failing ensemble tests** for nonnegative normalized weights, equal-weight shrinkage, family/strategy caps, zero weight on causal/promotion failure, delayed outcome updates, Fixed Share mass conservation, cost buffer, minimum breadth, and abstention.
- [ ] **Step 6: Run** `pytest tests/unit/test_strategy_ensemble.py -v` and verify failure.
- [ ] **Step 7: Implement evidence scoring and specialist Fixed Share/AdaHedge** with effective timestamps and persisted provenance.
- [ ] **Step 8: Write failing integration tests** that execute a small multi-strategy dataset end-to-end and assert current unlabeled inference works, every order follows its decision, weights use only resolved outcomes, and reruns are hash-identical.
- [ ] **Step 9: Implement orchestration and run all task tests plus Ruff.**
- [ ] **Step 10: Commit** with `feat: add evidence weighted strategy ensemble`.

### Task 6: Interpretable learning mode and trial ledger

**Files:**
- Create: `src/learning/__init__.py`
- Create: `src/learning/grammar.py`
- Create: `src/learning/search.py`
- Create: `src/learning/promotion.py`
- Test: `tests/unit/test_learning_grammar.py`
- Test: `tests/unit/test_learning_search.py`
- Test: `tests/integration/test_learning_mode.py`

**Interfaces:**
- Consumes: causal indicators, nested validation, execution engine, and learning tables.
- Produces: `RuleNode`, `RuleCandidate`, `LearningExperiment`, `LearningResult`, `discover_rules`, and `promote_candidate`.

```python
def discover_rules(experiment: LearningExperiment, development_bars: pd.DataFrame) -> LearningResult: ...
def promote_candidate(candidate: RuleCandidate, evidence: ForwardEvidence) -> PromotionDecision: ...
```

- [ ] **Step 1: Write failing grammar tests** for typed comparisons/crossovers/Boolean nodes, canonical serialization, semantic deduplication, maximum depth/node enforcement, deterministic mutation, and plain-language rendering.
- [ ] **Step 2: Run** `pytest tests/unit/test_learning_grammar.py -v` and verify failure.
- [ ] **Step 3: Implement the bounded causal grammar.**
- [ ] **Step 4: Run grammar tests** and confirm pass.
- [ ] **Step 5: Write failing search tests** proving a fixed seed is deterministic, every candidate is ledgered, fitness uses inner-fold net results plus penalties, final data is inaccessible to search, and early stopping respects the fixed evaluation budget.
- [ ] **Step 6: Run** `pytest tests/unit/test_learning_search.py -v` and verify failure.
- [ ] **Step 7: Implement evolutionary structure search plus bounded parameter search** without adding a heavyweight optimization dependency.
- [ ] **Step 8: Write failing integration tests** for experiment resume, failure recording, discovered-rule versioning, shadow state, and rejection of promotion without a new forward period and causal audit.
- [ ] **Step 9: Implement promotion flow and run all task tests plus Ruff.**
- [ ] **Step 10: Commit** with `feat: add interpretable strategy learning mode`.

### Task 7: Pipeline, CLI, reports, and snapshot schema v2

**Files:**
- Create: `src/strategies/pipeline.py`
- Create: `src/reporting/strategy_report.py`
- Modify: `src/cli.py`
- Modify: `src/demo.py`
- Modify: `src/app_snapshot/models.py`
- Modify: `src/app_snapshot/builder.py`
- Modify: `src/app_snapshot/writer.py`
- Test: `tests/integration/test_strategy_cli.py`
- Test: `tests/unit/test_app_snapshot.py`
- Test: `tests/integration/test_app_snapshot_export.py`

**Interfaces:**
- Consumes: strategy/learning engines and persistence from Tasks 1–6.
- Produces: typed CLI commands, streaming progress events, research report artifacts, and `AppSnapshot(schema_version=2)`.

```python
class StrategySnapshot(SnapshotModel): ...
class EnsembleComponentSnapshot(SnapshotModel): ...
class LearningRunSnapshot(SnapshotModel): ...

class AppSnapshot(SnapshotModel):
    schema_version: int = 2
    strategies: list[StrategySnapshot]
    ensemble_components: list[EnsembleComponentSnapshot]
    learning_runs: list[LearningRunSnapshot]
```

- [ ] **Step 1: Write failing CLI tests** for scoped ingest/evaluate/learn/export commands, safe strategy/mode validation, incremental refresh, `--force` recomputation by dataset/strategy version, and progress JSON.
- [ ] **Step 2: Run** `pytest tests/integration/test_strategy_cli.py -v` and verify failure.
- [ ] **Step 3: Implement registry-driven pipeline stages** while preserving legacy earnings commands and fixing the native `demo --mode` mismatch.
- [ ] **Step 4: Run CLI tests** and confirm pass.
- [ ] **Step 5: Write failing snapshot tests** for v2 strategy summaries, ensemble contributions, dataset coverage, learning trials/rules, causal audits, and crypto backtests when equity event data is absent.
- [ ] **Step 6: Run snapshot tests** and verify schema/fields fail.
- [ ] **Step 7: Implement strict Python v2 DTOs/builders and compact deterministic report export.**
- [ ] **Step 8: Run task tests, the existing Python suite, and Ruff.**
- [ ] **Step 9: Commit** with `feat: expose strategy research snapshot v2`.

### Task 8: Native SwiftUI Strategy Lab and typed engine requests

**Files:**
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Models/Snapshot.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Models/EngineJob.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Services/SnapshotRepository.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Services/EngineRunner.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/AppDestination.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/AppModel.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/RootView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/StrategyLabView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/StrategyDetailView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/LearningWorkspaceView.swift`
- Modify: `scripts/capture_macos_app.swift`
- Test: `macos/Nowcaster/Tests/NowcasterAppTests/SnapshotDecodingTests.swift`
- Test: `macos/Nowcaster/Tests/NowcasterAppTests/EngineRunnerTests.swift`
- Create: `macos/Nowcaster/Tests/NowcasterAppTests/StrategyLabTests.swift`

**Interfaces:**
- Consumes: snapshot schema v2 and typed CLI requests.
- Produces: `.strategyLab`, native selection/detail models, accessible research views, and live progress rendering.

```swift
enum EngineJob: Sendable, Equatable {
    case evaluateStrategies(strategyIDs: [String], mode: StrategyRunMode)
    case learn(assetID: String, interval: String, budget: Int)
    case exportSnapshot
}

struct StrategySnapshot: Decodable, Identifiable, Sendable {
    let strategyId: String
    let version: String
    let family: String
    let symbol: String
    let interval: String
    let state: String
    let weight: Double
    let developmentMetrics: [String: Double?]
    let finalTestMetrics: [String: Double?]
    let warnings: [String]
    var id: String { "\(strategyId)-\(version)-\(symbol)-\(interval)" }
}

struct LearningRunSnapshot: Decodable, Identifiable, Sendable {
    let learningRunId: String
    let state: String
    let evaluatedCandidates: Int
    let evaluationBudget: Int
    let bestRule: String?
    let finalBoundary: Date
    var id: String { learningRunId }
}
```

- [ ] **Step 1: Write failing Swift decoding and request tests** for every v2 DTO, schema compatibility behavior, argument-safe strategy/learning jobs, and incremental progress parsing.
- [ ] **Step 2: Run** `cd macos/Nowcaster && swift test --filter 'SnapshotDecodingTests|EngineRunnerTests'` and verify failure.
- [ ] **Step 3: Implement Swift v2 models, typed requests, and streamed runner events.**
- [ ] **Step 4: Run the focused tests** and confirm pass.
- [ ] **Step 5: Write failing Strategy Lab presentation tests** for navigation, selection, long/short/abstain disclosure, weights, evidence gates, learning state, no-repaint status, empty/unavailable states, and accessibility labels.
- [ ] **Step 6: Run** `cd macos/Nowcaster && swift test --filter StrategyLabTests` and verify failure.
- [ ] **Step 7: Build the Strategy Lab with native `NavigationSplitView`, `Table`, `Charts`, semantic styles, SF Symbols, keyboard navigation, and reduced-motion-safe progress.**
- [ ] **Step 8: Run the full Swift suite and `swift build -c release`.**
- [ ] **Step 9: Update the capture script and render Strategy Lab plus affected screens for visual QA.**
- [ ] **Step 10: Commit** with `feat: add native strategy lab`.

### Task 9: Full-history research run, documentation, CI, and cleanup

**Files:**
- Modify: `README.md`
- Create: `docs/strategy-methodology.md`
- Create: `docs/data-providers.md`
- Create: `docs/research-results.md`
- Modify: `.github/workflows/ci.yml`
- Modify: `.gitignore`
- Modify: `Makefile`
- Modify: generated snapshot fixture and compact research artifacts under existing repository conventions.
- Remove: obsolete daily-crypto implementation only after all remaining callers migrate and equivalence tests pass.
- Test: `tests/integration/test_full_strategy_research.py`

**Interfaces:**
- Consumes: all previous deliverables.
- Produces: reproducible full-history manifests/results, beginner documentation, CI drift checks, and a reduced source tree.

- [ ] **Step 1: Write a failing end-to-end test** that runs all enabled strategies on deterministic fixtures, checks every strategy receives a result or explicit unavailable reason, verifies the ensemble ignores failures, and validates snapshot/report consistency.
- [ ] **Step 2: Run** `pytest tests/integration/test_full_strategy_research.py -v` and verify failure.
- [ ] **Step 3: Add the deterministic research command and CI fixture profile** needed to make the test pass without network access.
- [ ] **Step 4: Run the end-to-end test** and confirm pass.
- [ ] **Step 5: Download/cache all available Binance history for enabled crypto symbols and intervals, generate manifests, execute every compatible strategy and learning-mode benchmark, and export compact research results; probe Alpaca credentials and otherwise mark equity intraday history unavailable with setup instructions.**
- [ ] **Step 6: Re-run the same research configuration** and verify dataset/config/code hashes and outputs are deterministic.
- [ ] **Step 7: Remove migrated dead code/data/config entries** only after `rg` proves no callers remain and the complete test suite remains green.
- [ ] **Step 8: Rewrite README and methodology/provider/results docs** for finance beginners, including examples, limitations, costs, overfitting, non-repainting, paper-trading workflow, and risk disclosure.
- [ ] **Step 9: Add CI checks** for Python, Swift, schema fixture drift, deterministic demo research, secret scanning, and generated-artifact cleanliness.
- [ ] **Step 10: Run Python tests/Ruff and Swift tests/release build.**
- [ ] **Step 11: Commit** with `docs: publish reproducible strategy research`.

### Task 10: Whole-product verification and publication

**Files:**
- Modify only files required by verified defects found during this task, with a failing regression test before each fix.

**Interfaces:**
- Consumes: the complete feature branch.
- Produces: reviewed release candidate on `main` and verified remote publication.

- [ ] **Step 1: Run the final branch review package** and resolve all Critical/Important findings through the subagent-driven review loop.
- [ ] **Step 2: Run fresh full verification:** `pytest`, `ruff check .`, snapshot regeneration plus clean-diff assertion, `swift test`, and `swift build -c release`.
- [ ] **Step 3: Run causal audits and deterministic full-history backtest rerun** and record exact dataset coverage and unavailable feeds.
- [ ] **Step 4: Render and inspect native screenshots** for Strategy Lab and affected screens at representative window sizes; fix visible/accessibility defects test-first.
- [ ] **Step 5: Inspect `git diff`, `git status`, Git history, ignored caches, large files, and credential patterns.**
- [ ] **Step 6: Use the finishing-development-branch workflow**, merge the verified feature branch into local `main`, repeat the core verification on merged `main`, and push to `origin/main`.
- [ ] **Step 7: Verify** `origin/main` resolves to the local commit and the GitHub repository shows the updated README and source tree.
