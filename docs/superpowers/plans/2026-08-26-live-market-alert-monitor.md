# Live Market Alert Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a notification-only macOS live market monitor for a configurable stock and crypto watchlist, with causal ensemble alerts, hypothetical entry/SL/TP levels, close/invalidation updates, and fail-closed background operation.

**Architecture:** A long-lived Python monitor consumes Alpaca equity and Binance spot streams, persists finalized bars and alert transitions, and emits bounded typed JSONL. A Swift actor supervises that engine, presents a native Live Monitor and menu-bar extra, and delivers deduplicated local notifications. The monitor has no broker mutation capability.

**Tech Stack:** Python 3.12, Pydantic, websockets, httpx, DuckDB/SQLAlchemy, Typer, pytest, Swift 6, SwiftUI, Observation, UserNotifications, ServiceManagement, Swift Testing.

**Spec:** `docs/superpowers/specs/2026-08-26-live-market-alert-monitor-design.md`

## Global Constraints

- Notification-only; no one-click or automatic order path.
- Finalized closed-bar decisions only; five-minute default decisions and finalized one-minute risk monitoring.
- Provider/feed identity must match promoted historical evidence; feeds and venues are never spliced.
- Missing eligibility, health, cost, calibration, or feasibility evidence yields abstention.
- Credentials remain in Keychain and a private bootstrap pipe; never argv, logs, persisted configuration, notifications, or Git.
- Existing broker execution and live-money locks remain unchanged.
- Local background monitoring is visible through a menu-bar extra and cannot claim operation while the Mac is asleep, offline, shut down, or quit.
- Every new behavior follows red-green-refactor and receives deterministic tests.

## File structure

- `src/live_monitor/types.py`: immutable wire, market, plan, health, and lifecycle models.
- `src/live_monitor/bars.py`: finalized-bar validation, aggregation, continuity, and replay-safe identities.
- `src/live_monitor/levels.py`: entry zone and SL/TP planner.
- `src/live_monitor/lifecycle.py`: monotonic alert state machine and notification requests.
- `src/live_monitor/repository.py`: DuckDB monitor session, bar, decision, setup, transition, and receipt persistence.
- `src/live_monitor/providers.py`: Alpaca/Binance frame normalization, subscription messages, reconnect posture, and bounded adapters.
- `src/live_monitor/engine.py`: watchlist orchestration, eligibility adapter, health breakers, and typed event emission.
- `src/live_monitor/command.py`: stdin bootstrap, JSONL protocol, signal handling, and CLI entry point.
- `macos/Nowcaster/Sources/NowcasterApp/Models/LiveMonitorModels.swift`: native protocol and presentation models.
- `macos/Nowcaster/Sources/NowcasterApp/Services/LiveMonitorService.swift`: child-process actor and bounded decoder.
- `macos/Nowcaster/Sources/NowcasterApp/Services/NotificationService.swift`: permission, categories, deduplication, and deep links.
- `macos/Nowcaster/Sources/NowcasterApp/Features/LiveMonitor/*`: control center, watchlist, setup detail, and menu content.
- Existing CLI, database schema, app model, settings, root navigation, application scene, package linker settings, packaging, and docs are modified at their established boundaries.

---

### Task 1: Typed live-monitor domain and level planner

**Files:**
- Create: `src/live_monitor/__init__.py`
- Create: `src/live_monitor/types.py`
- Create: `src/live_monitor/levels.py`
- Create: `tests/unit/test_live_monitor_types.py`
- Create: `tests/unit/test_live_monitor_levels.py`

**Interfaces:**
- Produces: `MarketBar`, `MarketQuote`, `MonitorHealth`, `TradeLevelPolicy`, `TradePlan`, `LifecycleEvent`, `MonitorWireEvent`.
- Produces: `plan_trade_levels(quote: MarketQuote, direction: Literal[-1, 1], atr: Decimal, structural_invalidation: Decimal, expected_targets: tuple[Decimal, ...], policy: TradeLevelPolicy) -> TradePlan | None`.

- [ ] **Step 1: Write failing model tests** proving explicit UTC, finite decimals, normalized symbols, positive OHLC, finalized bars, ordered long/short levels, and bounded wire payloads.

```python
def test_wire_models_reject_non_utc_and_impossible_levels():
    with pytest.raises(ValueError):
        MarketBar(provider="alpaca", feed="iex", symbol="AAPL", interval="1m",
                  start=datetime(2026, 8, 26, 10), end=datetime(2026, 8, 26, 10, 1),
                  open=Decimal("10"), high=Decimal("9"), low=Decimal("8"), close=Decimal("9"),
                  volume=Decimal("1"), finalized=True, revision=0, received_at=datetime.now(UTC))
```

- [ ] **Step 2: Run `pytest tests/unit/test_live_monitor_types.py -q` and confirm failure because `src.live_monitor` does not exist.**
- [ ] **Step 3: Implement frozen Pydantic models with `extra="forbid"`, explicit UTC/finite validation, enums, canonical IDs, and payload bounds.**
- [ ] **Step 4: Run the model tests and confirm they pass.**
- [ ] **Step 5: Write failing planner tests** with hand-derived long and short fixtures, spread/ATR noise rejection, reward-to-risk rejection, maximum-chase rejection, and outward tick rounding.

```python
def test_long_plan_has_executable_zone_stop_and_two_supported_targets():
    plan = plan_trade_levels(LONG_QUOTE, 1, Decimal("1"), Decimal("97"),
                             (Decimal("101.5"), Decimal("103")), POLICY)
    assert plan is not None
    assert (plan.entry_low, plan.entry_high, plan.stop, plan.target_1, plan.target_2) == (
        Decimal("100.00"), Decimal("100.10"), Decimal("97.00"), Decimal("103.00"), Decimal("104.50")
    )
```

- [ ] **Step 6: Run the planner test and confirm the missing-function failure.**
- [ ] **Step 7: Implement the smallest deterministic planner satisfying the policy and tests; return `None` rather than weaken a gate.**
- [ ] **Step 8: Run both focused files, Ruff them, and commit `feat: add causal live alert domain models`.**

### Task 2: Finalized bars, aggregation, and continuity

**Files:**
- Create: `src/live_monitor/bars.py`
- Create: `tests/unit/test_live_monitor_bars.py`
- Reuse: `src/strategies/calendars.py`

**Interfaces:**
- Consumes: `MarketBar`.
- Produces: `FinalizedBarLedger.accept(bar: MarketBar) -> BarAcceptance`, `aggregate_finalized(bars: Sequence[MarketBar], interval: BarInterval) -> tuple[MarketBar, ...]`, and `missing_ranges(...) -> tuple[BarRange, ...]`.

- [ ] **Step 1: Write failing tests** proving incomplete bars never enter, duplicates are idempotent, revisions do not mutate original identities, gaps are explicit, aggregation waits for every one-minute constituent, equity session bounds are calendar-aware, and appended future input cannot change prior aggregates.
- [ ] **Step 2: Run `pytest tests/unit/test_live_monitor_bars.py -q` and confirm missing APIs fail.**
- [ ] **Step 3: Implement immutable acceptance results, deterministic UTC bucket aggregation, and bounded missing-range detection.**
- [ ] **Step 4: Run focused tests and confirm pass.**
- [ ] **Step 5: Add a prefix-invariance property fixture comparing every prior output before and after appended future bars.**
- [ ] **Step 6: Run `pytest tests/unit/test_live_monitor_bars.py tests/unit/test_strategy_no_repaint.py -q`, Ruff, and commit `feat: add finalized live bar ledger`.**

### Task 3: Durable alert lifecycle and recovery

**Files:**
- Create: `src/live_monitor/lifecycle.py`
- Create: `src/live_monitor/repository.py`
- Modify: `src/database/schema.py`
- Create: `tests/unit/test_live_monitor_lifecycle.py`
- Create: `tests/integration/test_live_monitor_repository.py`

**Interfaces:**
- Consumes: `TradePlan`, eligible `MonitorDecision`, finalized risk bars.
- Produces: `AlertLifecycle.apply(event: LifecycleEvent) -> LifecycleTransition | None` and `LiveMonitorRepository` methods for sessions, finalized bars, decisions, setups, transitions, receipts, and recovery.

- [ ] **Step 1: Write failing state-machine tests** for candidate, entry, track-with-fill, TP1, TP2, stop, edge-decay close, reversal close-before-new-entry, expiry, unavailable health, terminal monotonicity, and duplicate event IDs.
- [ ] **Step 2: Run the lifecycle tests and confirm missing API failures.**
- [ ] **Step 3: Implement an explicit transition table; invalid or backward transitions raise without mutating state, duplicates return `None`.**
- [ ] **Step 4: Run focused lifecycle tests and confirm pass.**
- [ ] **Step 5: Write failing repository tests** using a temporary DuckDB and verifying restart replay, immutable prior transitions, unique event IDs, JSON round-trip, and no notification redelivery after a receipt exists.
- [ ] **Step 6: Add normalized monitor tables to `TABLES`, implement repository transactions through the existing `Database` abstraction, and make startup replay deterministic.**
- [ ] **Step 7: Run lifecycle/repository/schema tests, Ruff, and commit `feat: persist live alert lifecycles`.**

### Task 4: Provider adapters and health breakers

**Files:**
- Create: `src/live_monitor/providers.py`
- Add: `tests/fixtures/live_monitor/alpaca_stream.jsonl`
- Add: `tests/fixtures/live_monitor/binance_stream.jsonl`
- Create: `tests/unit/test_live_monitor_providers.py`

**Interfaces:**
- Produces: `AlpacaMarketDataAdapter`, `BinanceSpotAdapter`, `ProviderHealthTracker`, and `ReconnectPolicy`.
- Both adapters expose `subscription(symbols) -> dict`, `decode(message: bytes | str, received_at: datetime) -> tuple[MarketEvent, ...]`, and `stream(config) -> AsyncIterator[MarketEvent]`.

- [ ] **Step 1: Commit representative complete official frame fixtures** for success/auth/subscription, quote, finalized bar/kline, error, ping, and disconnect states; fixtures contain no secrets.
- [ ] **Step 2: Write failing decoder tests** for exact feed/symbol mapping, finalization, multiple events per Alpaca frame, Binance closed-kline enforcement, malformed/oversized payload rejection, and provider error classification.
- [ ] **Step 3: Run focused tests and confirm missing-adapter failures.**
- [ ] **Step 4: Implement pure subscription and decode boundaries first, with payload and symbol limits.**
- [ ] **Step 5: Add failing fake-server tests** for one multiplexed Alpaca connection, authentication timeout, Binance ping and planned 24-hour rotation, exponential bounded reconnect, and frozen health until continuity repair.
- [ ] **Step 6: Implement async stream loops using the pinned `websockets` API and existing bounded `httpx` patterns; keep credential strings out of errors.**
- [ ] **Step 7: Run provider tests, secret scan, Ruff, and commit `feat: stream live equity and crypto bars`.**

### Task 5: Monitor orchestration, eligibility adapter, and CLI protocol

**Files:**
- Create: `src/live_monitor/engine.py`
- Create: `src/live_monitor/command.py`
- Modify: `src/cli.py`
- Modify: `config/trading.yaml`
- Create: `tests/unit/test_live_monitor_engine.py`
- Create: `tests/integration/test_live_monitor_cli.py`
- Create: `tests/integration/test_live_monitor_replay.py`

**Interfaces:**
- Produces: `MonitorBootstrap`, `LiveMonitorEngine.run() -> AsyncIterator[MonitorWireEvent]`, `evaluate_alert_eligibility(...) -> MonitorDecision`, and Typer `monitor run`.
- Bootstrap arrives as one JSON line on stdin; stdout is typed JSONL only.

- [ ] **Step 1: Write failing engine tests** proving unpromoted/development cohorts abstain, provider/feed mismatch abstains, stale/gapped data freezes inference, shortability gates equity shorts, supported crypto shorts are venue-labelled, one active setup per scope, and no retrospective entries after recovery.
- [ ] **Step 2: Run focused tests and confirm missing-engine failures.**
- [ ] **Step 3: Implement a narrow adapter around existing ensemble evidence and readiness data; never copy or relax ensemble thresholds.**
- [ ] **Step 4: Implement orchestration with per-scope warm-up, health, finalized-bar evaluation, plan generation, lifecycle persistence, and heartbeats.**
- [ ] **Step 5: Write failing CLI tests** for stdin-only credentials, schema rejection, bounded watchlist, secret-free stderr, signal shutdown, JSONL stdout, and an architectural transport that fails if any non-GET external HTTP request is attempted.
- [ ] **Step 6: Add the `monitor` Typer group and `run` command; configure provider URLs, limits, freshness, grace, heartbeat, and retry ceilings without order endpoints.**
- [ ] **Step 7: Build a deterministic recorded-session replay that produces literal expected health and abstention/alert lifecycle events and proves repeat runs have identical IDs.**
- [ ] **Step 8: Run all live-monitor Python tests plus existing trading/strategy tests, Ruff, and commit `feat: add supervised live alert engine`.**

### Task 6: Native protocol, service actor, and settings

**Files:**
- Create: `macos/Nowcaster/Sources/NowcasterApp/Models/LiveMonitorModels.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Services/LiveMonitorService.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Features/Settings/SettingsView.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Security/BrokerCredentialVault.swift`
- Modify: `macos/Nowcaster/Package.swift`
- Create: `macos/Nowcaster/Tests/NowcasterAppTests/LiveMonitorModelsTests.swift`
- Create: `macos/Nowcaster/Tests/NowcasterAppTests/LiveMonitorServiceTests.swift`

**Interfaces:**
- Produces: `LiveMonitorEvent`, `LiveMonitorStatus`, `LiveSetup`, `WatchlistItem`, `LiveMonitorConfiguration`, `LiveMonitorServing`, and actor `LiveMonitorService`.
- Service exposes `events: AsyncStream<LiveMonitorEvent>`, `start(configuration:credentials:)`, `pause(reason:)`, and `track(setupID:fill:)`.

- [ ] **Step 1: Write failing decoder tests** for a hand-written complete wire fixture, unknown schema/type, non-UTC timestamp, non-finite number, excessive line/nesting/string/collection, duplicate sequence, and secret redaction.
- [ ] **Step 2: Run the focused Swift tests and confirm missing types fail compilation.**
- [ ] **Step 3: Implement strict `Codable` models and a bounded incremental JSONL decoder.**
- [ ] **Step 4: Write failing service tests** with a real executable fixture proving bootstrap goes to stdin rather than argv/environment, heartbeat timeout terminates the process, pause is orderly, crash changes health, and cancellation kills the child.
- [ ] **Step 5: Implement the actor using `Process`, separate stdout/stderr pipes, bounded diagnostics, exact bundled-engine resolution in release, and injectable test process factories.**
- [ ] **Step 6: Extend `AppSettings` with normalized stock/crypto watchlists, feed, intervals, monitor/resume/login toggles, quiet hours, and price privacy. Extend the vault with data-only credential retrieval while preserving paper/live separation.**
- [ ] **Step 7: Link UserNotifications and ServiceManagement, run focused and full Swift tests, and commit `feat: supervise native live monitor`.**

### Task 7: Native notifications, Live Monitor UI, and menu bar

**Files:**
- Create: `macos/Nowcaster/Sources/NowcasterApp/Services/NotificationService.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/LiveMonitor/LiveMonitorView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/LiveMonitor/LiveSetupDetailView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/LiveMonitor/LiveMonitorMenu.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/AppModel.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/AppDestination.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/RootView.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/NowcasterApp.swift`
- Create: `macos/Nowcaster/Tests/NowcasterAppTests/NotificationServiceTests.swift`
- Create: `macos/Nowcaster/Tests/NowcasterAppTests/LiveMonitorPresentationTests.swift`
- Modify: `macos/Nowcaster/UITests/NowcasterUITests.swift`

**Interfaces:**
- Consumes: `LiveMonitorServing`, `LiveMonitorEvent`, settings, and Keychain vault.
- Produces: `NotificationServing`, `LiveMonitorViewModel`, deep-link routing, `MenuBarExtra`, and accessible SwiftUI surfaces.

- [ ] **Step 1: Write failing notification tests** with an in-memory notification center for in-context permission, denied-state guidance, event-ID deduplication, foreground suppression, category enablement, quiet-hour entry suppression, tracked risk delivery, privacy-redacted copy, and deep-link metadata.
- [ ] **Step 2: Implement notification categories and service; no notification action may submit or imply an order.**
- [ ] **Step 3: Write failing presentation tests** for health labels/colors independent of color, watchlist normalization/errors, alert ordering, setup level copy, abstention reasons, tracking with actual fill, and notification-only disclosures.
- [ ] **Step 4: Implement the observable view model and Live Monitor views with native tables, inspectors, forms, SF Symbols, materials, selection, keyboard navigation, Dynamic Type, VoiceOver labels, and reduced-motion behavior.**
- [ ] **Step 5: Add sidebar navigation, notification deep links, and a `MenuBarExtra` exposing status, latest event, active count, Pause/Resume, Open, and Quit.**
- [ ] **Step 6: Implement opt-in `SMAppService.mainApp` registration and status/error presentation; auto-resume only when both launch and resume settings are enabled.**
- [ ] **Step 7: Add deterministic UI fixtures and UI tests for healthy, abstaining, reconnecting, permission-denied, active-long, and active-short states.**
- [ ] **Step 8: Run focused/full Swift and UI tests, build the native app, and commit `feat: add native live alert experience`.**

### Task 8: Packaging, security boundary, docs, and final verification

**Files:**
- Modify: `scripts/build_engine_bundle.sh`
- Modify: `scripts/build_macos_app.sh`
- Modify: `scripts/verify_production_release.sh`
- Modify: `Makefile`
- Modify: `README.md`
- Modify: `docs/architecture.md`
- Modify: `docs/data-providers.md`
- Modify: `docs/privacy.md`
- Modify: `docs/live-readiness.md`
- Create: `docs/live-monitor.md`
- Modify/Create tests under `tests/unit/test_engine_packaging.py`, `tests/unit/test_documentation.py`, and native accessibility/release tests.

**Interfaces:**
- Produces: a bundled monitor-capable signed engine, reproducible verification commands, beginner setup and limitations, and the final release evidence.

- [ ] **Step 1: Write failing packaging/security tests** proving `websockets` and monitor modules enter the engine bundle, the app links required frameworks, credentials do not appear in argv/config/log fixtures, and live-monitor imports/controlled traffic expose no broker mutation path.
- [ ] **Step 2: Update bundling, manifest, SBOM, signing, and release verification; build twice and compare manifests where determinism is expected.**
- [ ] **Step 3: Write beginner documentation** covering provider accounts/feeds, watchlists, starting/pausing, notification permission, interpreting confidence/entry/SL/TP/close, tracking fills, abstention, sleep/offline limitations, privacy, and troubleshooting without profitability claims.
- [ ] **Step 4: Add Make targets `verify-live-monitor` and a credential-free deterministic replay; keep credentialed smoke tests opt-in.**
- [ ] **Step 5: Run focused Python monitor tests and mutation checks, then the complete Python suite and Ruff format/check.**
- [ ] **Step 6: Run the complete Swift suite, native UI tests, engine bundle build, app build, manifest verification, `codesign --verify --deep --strict`, secret/history scan, SBOM validation, and production-release verifier.**
- [ ] **Step 7: Re-read the approved spec line-by-line, map every requirement to code/tests/docs, and fix every gap before proceeding.**
- [ ] **Step 8: Request independent code review against the spec, fix all Critical and Important findings, rerun the complete verification, commit `feat: ship live market alert monitor`, and push the reviewed branch to `origin/main`.**
