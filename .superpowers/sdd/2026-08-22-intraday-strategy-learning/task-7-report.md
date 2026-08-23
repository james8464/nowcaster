# Task 7 report: pipeline, CLI, reports, and snapshot schema v2

## Outcome

Task 7 is complete. The existing strategy and learning engines are exposed through a registry-driven, dependency-injectable pipeline and nested CLI. The native-app export is now a strict, deterministic schema-v2 snapshot with strategy, ensemble, dataset, learning, and causal-audit evidence. Legacy earnings commands remain available, and `export-app-snapshot` remains a compatible schema-v2 alias.

## Files changed

- `src/strategies/pipeline.py` — added typed stage options/results/events, safe provider and registry resolution, incremental ingestion, exact-key evaluation caching and force recomputation, Task 4/5 evaluation orchestration, Task 6 learning orchestration, durable promotion-boundary consumption, and snapshot/report export.
- `src/reporting/strategy_report.py` — added deterministic atomic aggregate research-report writing with the required cautionary language and no licensed raw bars.
- `src/cli.py` — added nested `strategy ingest`, `strategy evaluate`, `strategy learn`, and `strategy export`; retained all legacy commands; made the alias report the actual v2 schema; accepted native `demo --mode demo` while rejecting other demo modes.
- `src/app_snapshot/models.py` — added strict v2 strategy, ensemble, coverage, learning/trial/rule, and causal-audit DTOs; enforced `Literal[2]`, explicit UTC datetimes, finite-or-null numerics, bounded counts, and forbidden extra fields.
- `src/app_snapshot/builder.py` — added stable, bounded v2 section builders, conservative evidence/warning fields, legacy frozen-state rejection, source-backed provider posture, and crypto-only backtest export.
- `src/app_snapshot/writer.py` — preserved atomic replacement while making JSON compact, key-sorted, and non-finite-number rejecting.
- `tests/integration/test_strategy_cli.py` — added 11 CLI/pipeline tests.
- `tests/unit/test_app_snapshot.py` — added strict DTO/schema tests.
- `tests/integration/test_app_snapshot_export.py` — added v2 builder/export, ordering, frozen-state, and crypto-only coverage.
- `.superpowers/sdd/2026-08-22-intraday-strategy-learning/task-7-report.md` — this report.

`src/demo.py` did not require a production edit: the mismatch was at the CLI boundary, where `demo` previously lacked the native app's explicit `--mode demo` argument. The CLI fix preserves the existing keyless `run_demo` behavior.

## Behavior delivered

### CLI and pipeline

- New engine-facing commands emit JSON Lines compatible with Swift `EngineProgressEvent`: `event`, optional `stage`, `progress`, and `message`. Each path begins deterministically and ends in either `complete` or `error`.
- Provider, strategy, interval, and mode values are parsed by typed Pydantic DTOs and registry lookups. Invalid strings produce a structured terminal error; no dynamic import or YAML rewrite is used.
- Ingestion asks `BarRepository` for gaps, fetches only missing coverage, and appends immutable revisions. `--force` refreshes only the selected range and does not delete data. Unconfigured credentials/providers and missing returned data are reported as unavailable rather than as a successful evaluation.
- Evaluation uses all compatible local history, the Task 4 event-driven backtest and prefix-invariance audit, Task 5 outer/final boundaries and promotion gates, and only observed pre-boundary trial Sharpes. Failed cached runs are retried rather than reported as reusable.
- The exact evaluation key is dataset-manifest hash, strategy ID/version, symbol, interval, and mode. Forced or failed-run recomputation appends a new scoped run without altering unrelated dataset/version keys, even with an injected fixed clock. Run timestamps remain monotonic and lifecycle timestamps remain ordered.
- Strategy execution IDs contain dataset hash, strategy ID/version, symbol, interval, mode, decision timestamp, and execution timestamp, preventing legitimate natural-context collisions.
- Learning delegates indicator computation and discovery to Tasks 3 and 6, seals the outer final boundary before nested search, records observed bounded trials, and never feeds final-test results into parameters, weights, or learner feedback.
- The production promotion boundary writes the deterministic causal-audit consumption row transactionally before calling the pure Task 6 promotion helper. Consumption identity is global across modes for candidate, dataset, symbol, interval, and inspected period; the selected mode remains in audit details. Promoted and rejected decisions both spend the block, and repeated/concurrent use rejects.

### Snapshot and report

- `AppSnapshot.schema_version` is strictly `Literal[2]`; all legacy fields remain present.
- Task 8 minimum fields are present on `StrategySnapshot` and `LearningRunSnapshot`, with additional generation, progress, complexity, promotion, causal audit, no-repaint, trial, and rule details.
- Builders produce deterministic ordering and compact bounded histories: causal audits (500), trials (200 per run), discovered rules (500), and dataset coverage (200).
- Numeric values are finite or null, datetimes must be explicit UTC, unknown DTO fields are rejected, and learned rules with zero fitness sort correctly above negative-fitness rules.
- Legacy frozen metrics containing `online_state` fail with a regeneration instruction.
- Crypto `backtest_runs` export even when legacy equity event observations are zero.
- Metadata identifies source-backed `provider/feed` bar evidence and does not label CSV, Binance, or Alpaca strategy data as `demo_real_snapshot` merely because CLI settings use demo mode.
- Both snapshot and report use temporary files plus atomic replacement. Snapshot JSON is compact and key-sorted. The report contains only aggregate evidence/provenance and says it is a research/paper-trading aid, historical evidence is not live proof, uncertainty and abstention matter, and profit is not promised.

## TDD evidence

### Prescribed CLI RED/GREEN

- Initial RED: `pytest tests/integration/test_strategy_cli.py -v` — 6 collected, 6 failed.
- Expanded RED after adding native demo, force isolation, execution identity, truthful metadata, and promotion consumption cases — 10 collected, 7 failed, 3 passed.
- GREEN after pipeline/CLI implementation — 11 passed.

### Prescribed snapshot RED/GREEN

- RED: `pytest tests/unit/test_app_snapshot.py tests/integration/test_app_snapshot_export.py -v` — 8 collected, 7 failed, 1 passed.
- GREEN after DTO/builder/writer implementation — 8 passed.

### Hardening RED/GREEN

- Failed exact-key cache retry: 1 failed, then 1 passed after excluding failed runs from reusable cache results.
- Zero-versus-negative learned-rule ordering: 1 failed, then 1 passed after preserving zero as a real fitness.
- Fixed-clock force/retry identity and ordered lifecycle timestamps: each regression failed before its production fix, then passed.

## Final verification

- Focused Task 7 suite: `pytest tests/integration/test_strategy_cli.py tests/unit/test_app_snapshot.py tests/integration/test_app_snapshot_export.py -q` — **19 passed in 61.01s**.
- Existing Python suite: `pytest -q` — **421 passed in 85.54s**.
- Changed-file Ruff format/check — **passed**.
- `python -m compileall -q src` — **passed**.
- `git diff --check` — **passed**.

## Self-review

- Leakage: outer/final boundaries are sealed before validation/learning; final-test evidence is not used for learner feedback, parameters, or weights; observed trial Sharpes are filtered before the final boundary.
- Incremental key collisions: evaluation cache and run identity include the full required natural key; fixed-clock retries are monotonic; execution IDs include the full execution context.
- Force scope: tests preserve unrelated dataset and strategy-version sentinel rows and prove only the exact selected key receives another run.
- Atomic consumption races: audit identity intentionally omits mode, insertion occurs before promotion, the database primary key and transaction are the durable arbiter, and concurrent calls yield one winner/one rejection.
- Stable JSON ordering: DTO section ordering is explicit and serialized object keys are sorted; histories are bounded.
- Empty-table/crypto-only behavior: empty v2 tables build empty sections; crypto backtests do not depend on equity-event observations.
- Legacy compatibility: legacy CLI commands and keyless demo behavior pass the complete suite; `export-app-snapshot` now truthfully reports schema 2.
- Unsafe strings: provider/mode/interval enums plus registry resolution reject arbitrary imports, files, modes, providers, and strategy IDs.
- Source posture: provider/feed provenance is derived from persisted market bars, independent of the CLI's settings mode.

## Concerns and follow-up

- No known Task 7 blockers or correctness concerns remain.
- Live external Binance/Alpaca network calls were not exercised in the deterministic test suite; provider adapters are existing components and the new orchestration is covered through CSV and dependency-injected boundaries.
- Swift consumption is intentionally deferred to Task 8; no Swift files were edited.

## Independent-review fix round 1/5 addendum

All seven Important findings against `69347c85c7c370f5e1fdfe0bb69d36677759ce68` are addressed.

### Behavior and files

- `src/ingestion/bars.py`, `src/ingestion/binance_bars.py`, `src/ingestion/alpaca_bars.py`, and `src/strategies/datasets.py` now distinguish per-bar causal source availability from retrieval time. Initial historical bars become visible at close; changed refetches remain invisible until their retrieval timestamp.
- `src/strategies/pipeline.py` now derives one outer boundary from the raw finalized chronology, passes that exact boundary into learning and evaluation, and admits only observed adaptive trial evidence before it. Production learning evidence persists that boundary.
- Plural, validated strategy selections run together through Task 5. Current weights, contributions, resolved-outcome provenance, and the current ensemble decision are persisted without using final-block outcomes as feedback. The scalar `StrategyScope(strategy_id=...)` form remains compatible.
- `src/app_snapshot/models.py` and `src/app_snapshot/builder.py` now expose Task 8's wire shape: `bestRule` is a nullable string, structured rule evidence is separate, and `finalBoundary` is a required explicit UTC datetime. Real learning trials and discoveries durably carry the boundary.
- Forced evaluations atomically reserve a monotonic generation, timestamp, and collision-safe ID for the full cache key. Concurrent fixed-clock forces append distinct run and weight records while preserving unrelated scopes.
- Evaluation children and the running-to-evaluated transition commit in one transaction. A late child-write failure rolls back signals, executions, audits, weights, and the success transition; the reserved run is then marked failed without masking the original error, and a normal retry ignores that failed cache entry.
- `src/database/schema.py` adds an append-only requested-coverage ledger. Partial responses, unavailable providers, and empty forced refreshes are persisted and block evaluation/learning. The CLI ends these paths with structured `error` JSON Lines and a nonzero exit. Snapshot coverage uses the source-backed request range/gaps, while successful evaluation still uses all contiguous compatible local history across completed requests.
- `src/strategies/engine.py` and `src/strategies/ensemble.py` expose deterministic ensemble evidence rows so the pipeline can persist Task 5 output in the same atomic evaluation transaction.
- Regression coverage was added in `tests/unit/test_bar_ingestion.py`, `tests/unit/test_app_snapshot.py`, `tests/integration/test_bar_store.py`, `tests/integration/test_app_snapshot_export.py`, and `tests/integration/test_strategy_cli.py`. No Swift files were changed.

### RED/GREEN evidence

- Initial independent-review RED: `pytest tests/unit/test_bar_ingestion.py tests/unit/test_app_snapshot.py tests/integration/test_strategy_cli.py -q` — **10 failed, 21 passed (31 collected)**.
- Atomic-force hardening exposed two successive real races: the strategy CLI slice first ended **1 failed, 17 passed** on duplicate ensemble weight identity; the two-test concurrency/rollback slice then ended **1 failed, 1 passed** on shared audit insertion. After generation-scoped weights and atomic serialization, the slice passed **2 passed**.
- Source-backed incomplete-coverage snapshot RED: **1 failed** because the builder reported stored-bar end rather than the persisted requested end; GREEN: **1 passed**.
- All-local-history RED: **1 failed** because only the latest completed request's 40 bars were evaluated; GREEN: **1 passed** with all 80 contiguous local bars.
- Final focused fix-round suite: `pytest tests/unit/test_bar_ingestion.py tests/unit/test_app_snapshot.py tests/integration/test_bar_store.py tests/integration/test_app_snapshot_export.py tests/integration/test_strategy_cli.py -q` — **44 passed in 82.12s**.
- Full Python suite: `pytest -q` — **431 passed in 136.28s**.

### Fix-round self-review

- Live causality: adapter-shaped multi-bar history produces one unique decision per finalized bar, while corrected payloads preserve retrieval-time revision visibility.
- Boundary and leakage: raw chronology selects the boundary before indicator warmup/dropna; learning and evaluation persist the same identity; Task 5 feedback rows are restricted to outcomes available strictly before the sealed block.
- Ensemble context: plural inputs are enum/registry validated, homogeneous evaluation context remains enforced by Task 5, and persisted contribution/current-decision provenance is deterministic.
- Force/concurrency: the exact cache key includes dataset hash, strategy ID/version, symbol, interval, and mode; reservations include ordered generation/timestamp; execution IDs include the full natural context; unrelated sentinels remain unchanged.
- Atomicity: no successful cache entry is visible before child evidence commits, failed runs are not reusable, and failure-recording errors are attached as notes rather than replacing the originating exception.
- Availability: the latest requested range must be complete, the union of contiguous local compatible history must also be complete, and an empty forced refresh cannot reuse stale success.
- Snapshot contract: production learning snapshots decode with string `bestRule`, separate detail, and required UTC `finalBoundary`; coverage is bounded, deterministic, and request-backed.

### Remaining concerns

- No live external Binance or Alpaca network request was made in tests; their exact wire payloads were exercised through deterministic adapter-shaped mocked responses.
- Database concurrency tests use independent worker threads and real DuckDB transactions in one process, matching the native-app execution model. Cross-process DuckDB writers remain subject to DuckDB's own single-writer deployment constraints.
