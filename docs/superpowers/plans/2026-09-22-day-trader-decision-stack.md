# Day-Trader Decision Stack Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add causal, explainable paper-only market context and hypothetical lifecycle research to the live signal service.

**Architecture:** Immutable context snapshots gate the existing advisor before publication. A separate append-only lifecycle/evaluation layer records later hypothetical outcomes without changing an earlier decision or registered protocol. Native presentation is strictly read-only.

**Tech Stack:** Python 3.13, Pydantic, pandas, pytest, Swift 6, SwiftUI, Swift Testing.

**Spec:** `docs/superpowers/specs/2026-09-22-day-trader-decision-stack-design.md`

## Global Constraints

- Public Binance spot BTCUSDT/ETHUSDT only; long research or Stand aside only.
- All features, event data and lifecycle barriers must be finalized and causally available.
- No broker, credential, order, account, short posture, qualified alert or frozen-study path.
- Append-only protocol-bound evidence; no changed historical decisions or adaptive live weights.
- UI and notifications remain paper-only and non-actionable.

## Review Focus

- Future, stale, missing or revised context yields Stand aside.
- Incomplete timeframe buckets and pre-entry intrabar extrema cannot alter a decision/outcome.
- Event blackout and liquidity/volatility exclusions survive restart and native decoding.
- A poor live outcome cannot tune, delete or promote a candidate.
- Historical lifecycle views never appear as current postures.

### Task 1: Causal market-context contracts and feature extraction

**Files:** Create `src/research/day_trader_context.py`; test `tests/unit/test_day_trader_context.py`.

**Produces:** `MarketContextSnapshot`, `Regime`, and `extract_context(protocol, observations, decision_at, calendar)`.

- [ ] Write failing tests for future/incomplete 5m/15m bars, stale calendar data, spread, volatility, and order-book exclusions.
- [ ] Run `pytest tests/unit/test_day_trader_context.py -q` and confirm RED.
- [ ] Implement hash-bound 1m/5m/15m trend, realized volatility, spread, quote imbalance, session and calendar-blackout features; reject unavailable data rather than defaulting.
- [ ] Run focused tests and commit `feat: add causal day trader context`.

### Task 2: Context-gated postures and retained reports

**Files:** Create `src/research/day_trader_decision.py`; modify `src/research/live_paper_signal_runtime.py`; test `tests/integration/test_day_trader_decision.py`.

**Consumes:** `TrendAdvisorSuggestion`, `MarketContextSnapshot`. **Produces:** `DecisionContextReport`, `gate_suggestion(suggestion, context, now)`.

- [ ] Write failing tests proving volatile, illiquid, range, event-blackout, missing-context, and mismatched-hash inputs force Stand aside.
- [ ] Run RED with `pytest tests/integration/test_day_trader_decision.py -q`.
- [ ] Persist protocol-bound context reports and require timeframe agreement plus acceptable context before live publication; preserve exact context/advisor identity and expiry.
- [ ] Run focused tests and commit `feat: gate paper signals with market context`.

### Task 3: Non-repainting hypothetical lifecycles

**Files:** Create `src/research/day_trader_lifecycle.py`; test `tests/unit/test_day_trader_lifecycle.py`.

**Produces:** `PaperLifecycle`, `advance_lifecycle(lifecycle, observation)`, `LifecycleLedger`.

- [ ] Write failing tests for overlapping entry bars, target/invalidation ordering, regime change, time expiry and unavailable later observations.
- [ ] Run RED with `pytest tests/unit/test_day_trader_lifecycle.py -q`.
- [ ] Implement append-only hypothetical lifecycle records with conservative prospective barriers and no position, sizing, order or P&L surface.
- [ ] Run focused tests and commit `feat: record paper lifecycle outcomes`.

### Task 4: Fixed-variant anti-overfitting evaluation

**Files:** Create `src/research/day_trader_evaluation.py`; test `tests/unit/test_day_trader_evaluation.py`.

**Produces:** `VariantEvaluation`, `evaluate_variants(protocol, lifecycles, variants)`.

- [ ] Write failing tests for insufficient/in-sample-only samples, fixed costs, chronological folds, drawdown/turnover and multiple-testing penalties.
- [ ] Run RED with `pytest tests/unit/test_day_trader_evaluation.py -q`.
- [ ] Implement diagnostic-only chronological evaluation retaining all variants; prohibit protocol mutation or candidate promotion.
- [ ] Run focused tests and commit `feat: evaluate paper decision variants`.

### Task 5: Strict native decision-context presentation and documentation

**Files:** Create `macos/Nowcaster/Sources/NowcasterApp/Models/DayTraderModels.swift`, `Features/StrategyLab/DayTraderContextView.swift`; modify `StrategyLabView.swift`, `README.md`, `docs/research/live-paper-signals.md`; tests `macos/Nowcaster/Tests/NowcasterAppTests/DayTraderModelsTests.swift` and Python doc/contract tests.

- [ ] Write failing native tests rejecting expired/action-shaped/mismatched context and historical lifecycle records.
- [ ] Run RED: `cd macos/Nowcaster && swift test --filter DayTraderModelsTests`.
- [ ] Implement semantic HIG-native read-only cards for regime, agreement, exclusions and clearly historical hypothetical outcomes; update beginner docs with no profit/execution claims.
- [ ] Run `pytest -q`, full `swift test`, signed app build and codesign verification; commit `feat: present day trader decision context`.

## Plan self-review

- Tasks 1–2 cover causal context and gates; Task 3 prospective outcomes; Task 4 anti-overfitting evaluation; Task 5 strict native presentation and release verification.
- Each review-focus item has a named owning task and no task adds execution capability.
