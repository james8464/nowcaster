# Live Paper Study Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax for tracking.

**Goal:** Improve lower-turnover research and collect honest, frozen, prospective paper evidence from live public prices.

**Architecture:** Independent historical discovery feeds immutable study manifests. A separate SQLite paper ledger records live observations and hypothetical execution without touching broker evidence or qualified alerts. A resumable public-feed runner and Codex heartbeat keep the experiment observable.

**Tech Stack:** Existing Python 3.11+, pandas, NumPy, Pydantic, SQLite, httpx/websockets; existing Binance adapters and causal strategy library.

**Spec:** `docs/superpowers/specs/2026-09-08-live-paper-study.md`

## Global Constraints

- Paper only; no broker orders, credentials, qualified alerts, real-money readiness changes or forged forward evidence.
- BTCUSDT/ETHUSDT spot long/cash, independent $10,000 accounts; 25% notional cap and 0.25% initial-capital risk cap.
- All inspected history is development; retain every tested configuration and campaign, including failures.
- Prospective manifest frozen before collection; 90-day fixed end, no stop-on-first-profit; source mismatch fails closed.
- Use apply_patch and behavior-focused test-first development. Run focused tests per task, then one full final suite.
- Source implementations are isolated by file ownership; preserve unrelated changes and existing safety gates.

### Task 1: Bounded longer-horizon development search

**Files:** Create `src/research/holding_period_search.py`, `scripts/search_holding_periods.py`, `tests/test_holding_period_search.py`.

**Interfaces:** `search_scope(bars: pd.DataFrame, *, symbol: str, registry: StrategyRegistry) -> dict` returns all 12 configurations for that asset and `selected_candidate`. `run_search(root: Path, cache_dir: Path, output_dir: Path, start: datetime, end: datetime) -> dict` writes `discovery.json` and `report.md`. Candidate dict fields: `candidate_id` (canonical hash), `symbol`, `strategy_id`, `strategy_definition_hash`, `stop_atr` (1 or 2), `target_atr` (1.5 or 3), `maximum_bars` (6 or 12), `screen_passed` (bool). Discovery includes `candidates`, 24 trial rows, archive manifests/hash, fixed chronology/assumptions, explicit non-promotable status.

- [ ] Write failing tests for exactly 12 unique configurations per scope, stable identity, candidate chosen by worst fold rather than full sample, negative screen retained, future-data perturbation leaves earlier signals unchanged, costs and minimum target-distance exclusion. Use actual registry names (`squeeze`, `macd`, `volatility_scaled_trend` if those are the resolved IDs).
  ```python
  assert len(result['configurations']) == 12
  assert result['selected_candidate']['screen_passed'] is False
  assert result['promotable'] is False
  ```
- [ ] Observe targeted tests fail before implementation.
- [ ] Reuse `BinancePublicArchive`, `gap_safe_atr`, `simulate_opportunities` and existing generators; precompute each generator once. Divide chronology into four folds, preserving warmup causal context but preventing a scored trade crossing fold boundaries. Score all 24 before choosing at most one per asset. Keep 34/68 bps assumptions and 68 bps target-distance filter; use `target_atr=1.5*stop_atr`. Provide CLI with existing cache default `~/Library/Caches/NowcasterOpportunityAudit` and explicit exclusive cutoff. No full live strategy promotion.
  ```python
  rank = (min(f['mean_stressed_return'] for f in folds), full['mean_stressed_return'], candidate_id)
  screen_passed = all(f['trades'] >= 30 and f['mean_stressed_return'] > 0 for f in folds)
  ```
- [ ] Run focused tests and lint, self-review, commit only task-owned files. Do not run the expensive real archive search; the controller runs it after review.

### Task 2: Immutable prospective paper ledger

**Files:** Create `src/research/prospective_types.py`, `src/research/prospective.py`, `src/research/prospective_statistics.py`, `tests/test_prospective.py`, `tests/test_prospective_statistics.py`.

**Interfaces:** `StudyCandidate` validates Task 1 candidate dictionaries. `StudyManifest` stores schema_version=1, study_number, registered_at, starts_at, ends_at, source_hash, discovery_hash, candidates and frozen execution/risk constants from spec; `.study_id` canonical hash. `ProspectiveLedger(path: Path, manifest: StudyManifest)` owns an exclusive writer lock; context manager/close. `record_signal(candidate_id: str, *, decision_at: datetime, bar_end: datetime, reference_price: Decimal, atr: Decimal, signal_id: str)`, `on_quote(quote: MarketQuote, *, now: datetime, lot_step: Decimal, min_notional: Decimal)`, `record_gap(*, at: datetime, reason: str)`, `summary(*, now: datetime) -> dict`, `verify_integrity() -> bool`. Add simple read-only report function if needed. Summary has schema_version, study_id, status, paper_only=true, starts_at/ends_at/updated_at, candidates rows, limitation strings and evidence counts. Runner may call `summary` each minute and write via atomic_write_bytes.

- [ ] Write real SQLite tests covering restart exactly-once fills, conflict/mismatched manifest, exclusive writer, immutable decisions, no stale/early/undersized/seed fills, cash/risk/lot limits, ask/bid/fee arithmetic, stop/expiry latching, partial exits, gap tainting, hash corruption and early positive-result rejection. Hand-derive simple entry/exit amounts rather than using the production pricing helper for expectations.
  ```python
  assert ledger.summary(now=before_end)['status'] == 'collecting'
  assert summary['paper_only'] is True
  assert candidate['cash'] >= 0
  ```
- [ ] Observe failures before implementation. Implement focused models, transactional state and hashed journal, bounded duplicate memory, fresh quote validation and fixed-time assessment as specified. No mutation of existing monitor/trading repositories. Keep sampled minute valuations and fill quotes, not every market tick forever. Store closed trades including gap-tainted losses. Freeze all assumptions in manifest so omitted defaults are still hashed. End-of-study outstanding positions require observed liquidation; otherwise insufficient evidence.
- [ ] Statistics use complete UTC daily marked-to-market returns (not selective closed-trade-only days). Zero-return complete days count. At fixed end, evaluate at least 100 trades, 99% minute coverage, zero tainted trades, positive net/stressed P&L, drawdown <10%, and a positive 7-day moving-block bootstrap screening lower bound. Use 10,000 deterministic resamples; alpha `0.05/(study_number*(study_number+1)*len(candidates))`, reject insufficient tail resolution (<20 samples in alpha tail). Label approximate. A candidate that failed historical screen cannot pass. No automated real-money promotion.
- [ ] Run focused tests/lint, self-review, commit only task-owned files. Report exact schema and APIs for runner.

### Task 3: Public-feed runner and persistent operation

**Files:** Create `src/research/prospective_runtime.py`, `scripts/run_prospective_study.py`, `tests/test_prospective_runtime.py`; update README and `docs/live-paper-study.md`.

**Interfaces:** Consume Task 1 discovery and Task 2 ledger/models. CLI `register --discovery PATH --directory PATH`, `run --directory PATH --duration-seconds N`, `status --directory PATH`. Register creates `manifest.json`, campaign registry and `ledger.sqlite`; refuses overwrite and nonfuture registration. Run verifies source/discovery/strategy hashes before network and appends persistent evidence; writes atomic `summary.json` and human `report.md`. Default study directories remain outside repo under `~/Library/Application Support/Nowcaster/ProspectiveStudies`.

- [ ] Write failing runtime tests against real ledger plus fake transport at the network boundary: warmup generates no trade, only a complete freshly closed live hour produces a decision, repaired/old candles never trigger, reconnect records gap, changed source refuses before transport, persisted decisions survive restart, duration ends cleanly, fresh summary written and no credential/order APIs called. Test CLI registration twice rejects overwrite and retains registry counter.
- [ ] Seed finalized hourly context from public REST only; combine strictly complete live one-minute candles into hours using existing ledger/aggregator. Fetch missing context on restart, but never trade retroactively. Use existing registry, gap-safe ATR and signal construction identical to search (including target-distance floor). Verify symbol lot/minimum-notional metadata. Polling/streaming must not block summary/health heartbeat on a stalled feed. Bounded run ends gracefully with interrupted-position accounting, exclusive ledger writer ensures one collector. Record feed unavailable and clock/gap problems, never silently claim full coverage.
  ```python
  if live_hour.end > manifest.starts_at and now - live_hour.end <= timedelta(seconds=5):
      # Frozen, causal history only; no seed or repair can emit an intent.
      ledger.record_signal(...)
  ```
- [ ] Add novice documentation: paper cash vs money, fixed study horizon, read outcomes/costs/coverage, Mac-awake limitation, stop/resume commands, failed history disclosure, no profit promise; link full evidence reports.
- [ ] Run focused tests/lint then controller performs full verification, actual historical search and live smoke. Controller registers frozen manifest only after source is final, collects actual quotes and schedules product heartbeat to resume/check; no OS daemon or power-setting changes. Rebuild provenance fixtures if source hash changes, build/package native app and push authorized changes after review.
