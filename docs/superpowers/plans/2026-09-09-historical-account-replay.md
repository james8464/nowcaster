# Historical Account Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a retained, chronological paper-account replay of the two existing BTC/ETH rules and explain their historical after-cost performance.

**Architecture:** A pure bar-by-bar engine shares one causally generated decision stream between base/stress paper accounts. A separate runner restores/verifies pinned archives, registers the diagnostic attempt, persists evidence and publishes retrospective reports. Existing strategy generators and archive parser are reused; the live study is untouched.

**Tech Stack:** Python3.13, existing pandas/Pydantic/httpx, stdlib Decimal/hashlib/json/fcntl. No new dependencies or native UI changes.

**Spec:** `docs/superpowers/specs/2026-09-09-historical-account-replay-design.md`

## Global Constraints

- Do not modify `.worktrees/live-paper-study`, its environment, the live collector, or anything in `ProspectiveStudies`.
- No installs, credentials, orders, power-setting changes or OS daemons. Use the existing Python environment read-only, with bytecode disabled.
- No strategy/parameter selection, fitting or tuning in this round. Use the two candidates retained in `data/research/holding-period-search-2026-09-08.json` unchanged.
- Original discovery SHA-256: `58c25ec6e836ecd7b17963a03ef6e9f3e43a03b78666c534506b874699a5f903`.
- Historical candle availability is modeled at candle close. Archives can be revised and are not point-in-time vintages. No historical quote/queue/latency/partial-fill fidelity claim.
- Every attempt, error and partial event log survives. Refuse to reuse or overwrite an output directory. Do not alter the prospective campaign registry.

---

### Task 1: Causal historical account engine

**Files:** Create `src/research/historical_replay.py`, `src/research/historical_replay_account.py`, `tests/unit/test_historical_replay.py`.

**Interfaces:**

```python
class HistoricalReplay:
    def __init__(self, candidate: dict, registry, *, on_event=None): ...
    def push_bar(self, bar: dict) -> None: ...
    def result(self) -> dict: ...
```

Consumes archive-parser row dictionaries (one hourly asset), candidate definition including retained screen flag, and existing StrategyRegistry. `on_event(event:dict)` receives immutable JSON-safe hash-chain events. `result()` returns JSON-safe `{candidate, bars_seen, decision_count, journal_head, last_at, scenarios:[...]}`. Each scenario has `cost_multiplier`, `initial_cash`, `cash`, `marked_equity`, `realized_pnl`, `net_pnl`, `fees`, `spread_cost`, `slippage_cost`, `maximum_drawdown`, `trades` (completed records), `open_position`, `pending_entry`, `counts`, `equity_curve` (rows `{at,cash,equity,...}`). All monetary values are decimal strings. Trade records include original decision identity and plan, quantities, opening/closing interval, exit reason, entry/exit cash flows, net P&L, costs and taint. Final result does not mutate state or force liquidation. Only these files are owned by Task1.

- [ ] Write failing tests using synthetic hourly dictionaries and a real StrategyRegistry with a deterministic test generator. Hand-check entry and exits, for example an already accepted long with ATR1 and entry raw100: modeled entry100.070010, entry unit cost100.170080010; a stop one unit below that entry sells with base factors `.9998*.9995` and fee`.001`. Derive expected quantities independently with Decimal constants, not the production helper. Test target, expiry (original bar close+12h), ambiguous stop-first, adverse opening gap, idle cash, successive sizing after a loss, costs1x/2x, minimum size rejection, retained open ending and original plan not replaced by busy signals.
- [ ] Run focused tests and retain expected RED failures before implementation. Use existing `/Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.venv/bin/python -m pytest -q tests/unit/test_historical_replay.py` with `PYTHONDONTWRITEBYTECODE=1`.
- [ ] Implement two logical phases per incoming bar. Preserve one pending decision from earlier closed context; entry uses only new opening fields. At close resolve existing barriers, observe/validate completed data, calculate on the most recent1000 prior/current bars using existing `eligible_long_signals`/`gap_safe_atr`, freeze decision values, then mark accounts. Do not precompute future signals. Share the calculation between the two independent cost scenarios. Validate UTC, hourly duration, provider/feed/symbol, positive finite consistent OHLC, volume nonnegative, finalized, unique/increasing nonoverlap and `available_at==close_timestamp`; reject unsupported late/revision events without rewriting earlier records.
- [ ] Implement account arithmetic exactly as the spec, using Decimal, explicit lot increments and no future volume/price eligibility. On missing time, cancel pending and liquidate held quantity at the first known opening with taint; retain the loss. Keep incomplete terminal positions/pending signals.
- [ ] Write RED then GREEN future-isolation tests. Compare all prior event records under (a) appended bars, (b) radically changed future OHLC/volume and (c) attempting a revised prior timestamp. Assert that changing a newly entered bar's later HLC/volume cannot change its entry evidence. Verify actual configured BTC/ETH decisions match direct existing-generator evaluation of the same prefix/window, including gaps and the1000-bar cap. Preserve original `reason` and record input bar-hash window bounds. Entry hashes must not bind future HLC/volume.
- [ ] Run the focused file plus `tests/test_holding_period_search.py`; format/check owned files, self-review and commit. Record exact commands/RED-GREEN output in the assigned report. Benchmark a small real-generator fixture and report seconds/bar; do not run real historical data yet. Root owns the full release suite.

### Task 2: Registered runner, pinned data restoration and result reporting

**Files:** Create `scripts/run_historical_replay.py`, `src/research/historical_replay_reporting.py`, `tests/test_historical_replay_runner.py`. Root separately owns README and dated study-report documentation.

**Interfaces:** Consume Task1 `HistoricalReplay(candidate,registry,on_event=...)`, `.push_bar(dict)` and `.result()` shape above. CLI arguments: `--root`, `--cache-dir`, `--output-dir` (required), `--allow-download` (default false), `--workers` (integer1or2, default1). Public runner `run_replay(root:Path, cache_dir:Path, output_dir:Path, *, allow_download:bool=False, workers:int=1) -> dict`. Reporter `render_report(result:dict) -> str`, with explicit retrospective and execution-limit text.

- [ ] Write tests for refusal to overwrite existing output, pinned discovery/hash and candidate-definition mismatch, malformed manifests/path traversal, missing archive offline, changed ZIP/checksum, output under source/cache/prospective data, retained registered failure, repeat attempt numbering and machine-readable finished result. Use temporary real archive ZIP+CHECKSUM fixtures and a minimal discovery passed through a lower-level loader helper; keep production pinned discovery strict. Fake network only at the HTTP transport boundary. Run RED first.
- [ ] Implement protocol registration in a separate HistoricalReplays parent using an append-only hash-chained `campaigns.jsonl` under file lock. Bind hypothesis `fixed_selected_rule_account_replay_v1`, source/runtime identity, retained discovery SHA and prior24-trial selection history, two unchanged candidates, exact archive list/window, model assumptions and no-promotion/retrospective flags. Record before reading outcomes. Create a fresh directory and persist protocol/status; fail/refuse safely on existing files and invalid registry. Errors/interruptions persist failed status and events; never erase attempts. No unsafe directory recursion or broad cleanup.
- [ ] Implement exact archive preflight/restoration using existing `BinancePublicArchive`: validate all manifest fields and derived paths, restore only missing exact ZIP/.CHECKSUM pairs from official Binance paths when explicitly enabled, require old pinned digest, byte length and matching checksum filename. In offline mode, missing files error before invoking a network transport. After preflight use a raising network transport with the public `fetch` API to load both hourly scopes; compare returned manifest rows exactly and require no unavailable files. Match retained row counts/invalid-boundary counts and reject duplicate/invalid bars. Do not replace changed archives with another dataset. Report restoration failures plainly.
- [ ] Load registry from config without account/environment access using existing `load_registry`. Stream rows into Task1 one at a time, write per-asset `events.jsonl` through `on_event`, print bounded progress each1000bars and flush. Run assets sequentially by default to bound CPU/memory; no full historical reruns in tests. Publish result JSON and Markdown only on success after source identity recheck. Output files use exclusive creation; any partial file remains clearly non-success in status. Include source/runtime/archive identities and explicit observations/window/gap quality profile.
- [ ] Derive monthly/yearly equity changes from observed-close curves with initial10000 baseline, label partial2017/2026 years, and keep realized/marked outcomes and both independent scenarios distinct. Include completed counts, wins/losses, costs, drawdown, ambiguities, tainted trades and open/pending exposure. Never add accounts, label an inspected tail independent, or invent confidence. Test year boundaries, loss retention, empty trades and no division-by-zero; real integration test with controlled archive/engine paths must produce reproducible evidence.
- [ ] Apply the interval-ending period convention (midnight close belongs to the hour/month/year just ended), carrying equity across boundaries. Distinguish calendar-window completeness from missing-hour coverage, and the doubled-cost scenario from prospective extra34bps stress. Archive downloads may use up to four HTTP workers; the preflight-complete loader must then use a raising network transport. Evaluation defaults to one worker, with optional two isolated asset processes, separate logs and deterministic result order; cap at two while reserving at least two logical processors and recording requested/effective workers. Test worker limits, actual parallel worker dispatch on small synthetic data and equality with sequential results; never split one account's chronology. Worker failures must leave the overall attempt failed and other partial evidence retained.
- [ ] Run focused runner/report tests and Task1 tests; Ruff/check/format; self-review; commit and supply RED-GREEN report. Root owns full release verification and actual evaluation.

## Controller execution and release checklist

- [ ] Verify clean isolated baseline and record plan identity/preflight interface table in ignored progress ledger.
- [ ] Complete Task1 review, then Task2 review, then one broad final review with one combined correction wave if needed. Preserve all review reports.
- [ ] Freeze source and protocol before actual historical evaluation. Run CLI once against a new separate `HistoricalReplays/round-001-20260909` directory with a separate cache and `--allow-download`; retain every result/failure. If a correction becomes necessary after results were viewed, record a separate attempt with reason and prior attempt retained, never silently replace it.
- [ ] Independently reconcile result cash/equity/cost identities, year totals, gap counts and original discovery bindings. Explain exact historical results in `docs/historical-account-replay-2026-09-09.md` and README. Method plus raw evidence paths are the reproducible analytical artifact.
- [ ] Verify new-source full Python suite once; deterministic fixture parity/repeatability and native/build checks as required for changed source identity. Confirm live collector/source/environment unchanged. Merge/upload under standing user authorization; retain study and audit artifacts. Track new CI without repeating completed tests, searches or unchanged pending updates.
