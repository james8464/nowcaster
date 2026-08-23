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

## Independent-review fix round 2/5 addendum

All seven Important findings against `db73982cf6e4303b7afa19c5769395ccf2277bb0` are addressed.

### Behavior and files

- `src/strategies/datasets.py` and `src/strategies/library.py` now expose an eligible revision ledger for point-in-time signal generation. First-observed causal prefixes remain immutable; a corrected revision becomes a new receipt-time decision without rewriting the earlier signal rows. Execution and sealed-boundary bars are separately resolved from the earliest observable revision of each logical bar.
- `src/ingestion/binance_bars.py` and `src/ingestion/alpaca_bars.py` capture retrieval time only after each successful HTTP response. Every pagination page therefore has its own post-response receipt timestamp, while initial historical rows still use causal close availability and repository revisions remain retrieval-protected.
- `src/strategies/pipeline.py` persists and reuses only a complete exact cohort: sorted strategy ID/version/family membership, dataset/context/mode/as-of, ensemble policy, configured caps, and validation policy are hashed together. Scalar component runs cannot satisfy a plural request; every member in a reusable cohort shares one effective time and decision hash.
- `src/strategies/ensemble.py` supports immutable family-specific caps. `create_strategy_pipeline` translates `strategy_weight_cap` and `family_weight_caps` from validated settings into Task 5, and the full policy is stored in run and ensemble provenance. Infeasible cap combinations retain Task 5's explicit rejection behavior.
- `src/database/schema.py` adds append-only signal/execution link tables. Each forced strategy-run generation links to its complete immutable child evidence set in the same transaction as the evaluated transition, without overwriting shared natural-key evidence.
- Coverage is reserved as `running` before provider I/O and atomically finalized. Exceptions preserve the original failure while durably making the newest request unavailable, partial results remain incomplete, stale successful requests cannot be reused, and recovery creates a newer complete request.
- `src/strategies/calendars.py` supplies deterministic offline `XNYS` and continuous schedules. Alpaca gaps exclude closures, weekends, and US exchange holidays while still detecting missing regular-session bars; calendar ID/version are included in manifest hashes, coverage evidence, and snapshot DTOs. Synthetic off-session fixtures retain local between-observation gap detection without treating real closures as missing.
- `src/app_snapshot/builder.py` selects the newest complete ensemble cohort rather than mixing independently latest component rows, and exports coverage calendar provenance. `src/app_snapshot/models.py` strictly exposes the new calendar fields.
- Regression tests were added to `tests/unit/test_bar_ingestion.py` and `tests/integration/test_strategy_cli.py`; existing bar-store and strategy-library tests were also rerun to guard compatibility. No Swift files were edited.

### RED/GREEN evidence

- Initial round-two RED: `pytest tests/unit/test_bar_ingestion.py tests/integration/test_strategy_cli.py -q` — **8 failed, 29 passed (37 collected) in 25.17s**. Failures covered both provider clocks, revision point-in-time decisions, failed-fetch state, exchange-calendar gaps, run-scoped links, scalar-to-plural cohort reuse, and cap-policy invalidation.
- Focused round-two GREEN: the same command — **37 passed in 45.09s**.
- The first complete-suite run exposed two legacy compatibility failures — **2 failed, 438 passed in 140.55s** — for synthetic Saturday Alpaca observations and an out-of-order initial bar. Both received regressions-preserving fixes; their combined strategy/bar/Task-7 slice then passed **81 passed in 46.70s**.
- Final complete Python suite: `pytest -q` — **440 passed in 136.54s**.
- Changed-file Ruff check — **passed**.
- `python -m compileall -q src` and `git diff --check` — **passed**.

### Fix-round self-review

- Revision leakage/no-repaint: signal events are ordered by actual availability and have unique decision timestamps. A first-delivered old bar cannot retroactively trade a later bar; an actual revision is visible only at its receipt event. Execution/validation chronology never substitutes a late correction into its original historical point.
- Cohort/cache identity: membership includes deterministic strategy versions and is sorted; dataset, symbol, interval, mode, as-of, validation policy, ensemble policy, and cap values all participate. Reuse requires the complete matching generation with one decision hash/effective time.
- Cap feasibility: settings validation rejects a strategy cap above its configured family cap; Task 5 rejects total-capacity infeasibility instead of silently renormalizing beyond caps. Provenance records the exact accepted policy.
- Run evidence: signal and execution IDs preserve their natural identities, while append-only run-link rows provide generation-specific auditability. The evaluated state, links, children, audit, and weights share one transaction.
- Fetch lifecycle: a newest failed/partial request blocks evaluate/learn even if older coverage succeeded; a later complete request recovers normally. Provider exceptions are re-raised unchanged, with persistence failures attached only as notes.
- Calendar evidence: Alpaca uses a source-specific XNYS ruleset with DST and deterministic holiday handling; continuous crypto coverage remains 24x7. Calendar identity/version is sealed into both dataset and exported coverage provenance.
- Snapshot stability: only a complete newest cohort is exported, ordering remains deterministic, and calendar additions preserve strict DTO validation.

### Remaining concerns

- The offline XNYS schedule models regular sessions and full-day US exchange holidays but not exceptional one-off closures or early-close session shortening. Its version is persisted so a future audited calendar upgrade invalidates cache identity deterministically.
- Live network latency was simulated with ordered response/clock hooks; no external Binance or Alpaca request was made.

## Independent-review fix round 3/5 addendum

All five Important findings against `5bc6224417a7af95a6f21e90073d40b5082746d8` are addressed.

### Behavior and files

- `src/strategies/pipeline.py` no longer aligns revision-aware signal rows to causal bars by ordinal position. It assigns every decision a deterministic source hash, maps it to the actual next executable bar under Task 4's timing rule, collapses all states effective for that bar to the final state, and derives return/cost from that execution bar. Feedback passed to Task 5 now includes execution timestamp plus source-decision and source-execution hashes. The exact compact hash ledger is persisted with each cohort's weight evidence.
- Evaluation cache-check and reservation are one sorted-cohort critical section. Every forced cohort receives one monotonic cohort generation and one effective timestamp before all component `strategy_runs` are inserted in a single transaction. Concurrent plural evaluations therefore cannot cross-pair component generations; later non-force evaluation reuses the newest complete evaluated cohort.
- Coverage reservation is serialized on provider/feed/symbol/interval/requested-range identity, retries generation/timestamp/ID conflicts, and releases the reservation lock before provider I/O. Concurrent immutable-bar append is separately serialized by instrument/feed identity. Two fixed-clock forced ingests now retain independent pre-I/O request rows and independent terminal states.
- `src/strategies/calendars.py` now implements Alpaca-compatible aggregation labels: minute buckets use UTC-aligned interval starts, hourly/multi-hour labels follow clock-hour aggregation even when the first/last regular-session bucket is partial, and `1Day` has one New York-local midnight label per session. Session-aware closes cap partial bars, daily bars become finalized at the exchange close, and the version is `offline-rules-2026.2`.
- The deterministic XNYS rules include the 2026 full-day holidays and repeatable early closes after Thanksgiving, on eligible July 3 sessions, and on eligible December 24 sessions. Black Friday 2026 ends at 13:00 ET. The implementation is based on Alpaca's Market Data FAQ (`https://docs.alpaca.markets/us/docs/market-data-faq`) and NYSE's published hours/calendar (`https://www.nyse.com/trade/hours-calendars`).
- `src/ingestion/alpaca_bars.py` uses the source calendar to determine finalized close/availability, so daily and partially aggregated historical bars are no longer dropped by a fixed-duration close that extends beyond retrieval time.
- `src/app_snapshot/builder.py` queries ensemble rows newest-first without applying an input cutoff, scans backward for the newest complete cohort with exact membership and one decision hash, never substitutes independently latest rows when cohort evidence exists, and applies the 1,000-component output bound only after cohort selection.
- New regressions live in `tests/integration/test_strategy_cli.py`, `tests/unit/test_bar_ingestion.py`, and `tests/integration/test_app_snapshot_export.py`. No Swift files were edited.

### RED/GREEN evidence

- Initial focused RED: six tests covering the five findings — **6 failed in 17.38s**. The failures were missing execution provenance/positional outcomes, absent atomic cohort generation, a duplicate fixed-clock coverage key, incorrect 09:30-anchored hourly labels, a discarded daily bar, and incomplete snapshot-cohort fallback.
- The coverage concurrency test was tightened to capture both worker exceptions as behavior; it failed with a duplicate-key `OperationalError` plus the blocked peer, proving the request reservation race before provider I/O.
- Focused GREEN: the same six regressions — **6 passed in 7.24s**.
- High-risk provider/Task 4/pipeline/bar-store/snapshot slice — **73 passed in 141.51s**.
- Complete Python suite: `pytest -q` — **446 passed in 172.14s** on the final verification run.
- Changed-file Ruff check, `python -m compileall -q src`, and `git diff --check` — **passed**.

### Fix-round self-review

- Outcome causality: post-correction ordinary decisions retain their literal next-bar outcome; multiple decisions eligible for one execution bar yield exactly one final state; corrections never shift later feedback by row position. Feedback ends before the sealed final boundary and contains no final-result learner input.
- Provenance: decision hashes seal dataset, strategy/version, symbol, interval, mode, timestamps, signal, and strength. Execution hashes seal the exact causal bar identity, timestamps, and payload hash. Persisted cohort evidence seals the ordered provenance records with a canonical aggregate hash.
- Cohort atomicity: membership remains sorted in the cohort identity; cache lookup and allocation share one lock; component reservations share generation and timestamp; all reservation rows commit together. Failure metrics preserve the cohort generation so retry allocation cannot reuse it.
- Coverage atomicity: reservation remains before network I/O, provider work is not performed while holding the identity lock, fixed clocks monotonically advance persisted request timestamps, and immutable bar deduplication is serialized independently from coverage lifecycle state.
- Calendar/provider alignment: regular-session gaps skip overnight/weekend/holiday intervals, still flag a missing in-session bucket, and handle EST/EDT label changes. Early closes and calendar version participate in dataset hashes and exported coverage evidence.
- Snapshot coherence: input history is not truncated before validation; incomplete newest cohorts are skipped; no per-component fallback occurs when cohort metadata exists; only one decision hash/effective cohort is exported.

### Remaining concerns

- The offline calendar intentionally covers repeatable NYSE holiday/early-close rules, not emergency or one-off closures. A future authoritative calendar update must bump the persisted version, which deterministically invalidates affected dataset/cohort identities.
- Alpaca and NYSE behavior was verified against their official documentation with deterministic fixtures; no credentialed live request was made.

## Independent-review fix round 4/5 addendum

All four Important findings against `bae60e72159f52a5626d4f21a4a89fd468199874` are addressed.

### Behavior and files

- `src/strategies/pipeline.py` now derives adaptive ensemble feedback from Task 4's actual fill ledger and its immutable `source_decisions`, rather than independently matching signal timestamps to the first chronological bar. Zero-volume/capacity-delayed transitions inherit Task 4's real execution timestamp and decision provenance. The resolved execution hash is the same full-context ID persisted for that Task 4 execution.
- Feedback is eligible for development learning only when both the actual Task 4 execution timestamp and its causal bar outcome availability are strictly before the sealed `final_start`. A decision made before the boundary but delayed into the final block is therefore excluded.
- Cohort reservation acquires the cohort lock and every full component cache-key lock in one deterministic global order. Overlapping `[A, B]` and `[A]` reservations cannot allocate the same component timestamp/identity, while disjoint cohorts remain independent. Transient integrity/operational conflicts re-read cache and generation state before retrying. A non-retryable or exhausted reservation-write exception creates terminal failed component-run records with explicit `reservation_error` evidence before the original exception is re-raised.
- `src/strategies/calendars.py` replaces generic nearest-weekday New Year handling with the XNYS rule: Sunday January 1 is observed Monday, while Saturday January 1 is not observed on Friday. December 31, 2027 remains a full regular session for January 1, 2028. The persisted calendar version is now `offline-rules-2026.3`.
- `src/app_snapshot/builder.py` uses a stable total cohort order over effective time, dataset hash, cohort ID, and component context before scanning for the newest complete cohort. Equal-time complete cohorts export identically regardless of database insertion order, while the previous newest-complete fallback and post-selection bound remain intact.
- Regressions were added to `tests/integration/test_strategy_cli.py`, `tests/unit/test_bar_ingestion.py`, and `tests/integration/test_app_snapshot_export.py`. No Swift files were edited, and the ignored progress ledger was preserved.

### RED/GREEN evidence

- Initial focused RED: five tests covering the four findings and the explicit reservation-failure lifecycle — **5 failed, 0 passed in 5.90s**. Failures were the incorrect Friday New Year closure, pre-actionability feedback timestamp/hash, an overlapping-cohort duplicate key, zero durable failed reservations, and insertion-order-dependent snapshot selection.
- Focused GREEN after the production fixes: the same five regressions — **5 passed, 0 failed in 5.77s**.
- Retry/re-read hardening RED: an injected first-attempt integrity conflict — **1 failed** because it immediately entered failure lifecycle instead of retrying. GREEN with the overlap/failure/concurrent-plural slice — **4 passed in 9.79s**.
- The first surrounding Task 7 compatibility run produced **1 failed, 52 passed in 155.11s**: the earlier correction test still expected timestamp-only feedback at a bar where Task 4 had no fill. Its assertion was updated to require no unactionable feedback and unique execution provenance.
- Final combined actionability/correction/concurrency/calendar/snapshot slice — **6 passed in 8.73s**.
- Complete Python suite: `.venv/bin/pytest -q` — **452 passed in 158.30s**.
- Changed-file Ruff check and format check — **passed**.
- `git diff --check` — **passed**.

### Fix-round self-review

- Feedback causality/leakage: the source signal comes from the fill's Task 4 state-transition provenance; zero-volume delays are not re-simulated in the pipeline. Both execution and outcome availability must precede the frozen boundary, so a pre-boundary decision cannot leak a delayed final-block result into Task 5 weights.
- Execution provenance: `_strategy_execution_id` is shared by feedback resolution and persistence, and seals dataset, strategy ID/version, symbol, interval, mode, decision timestamp, and actual execution timestamp.
- Overlapping concurrency: component lock identities contain the full evaluation cache key and are acquired together with the cohort lock in sorted hash order, preventing lock inversion/deadlock and duplicate component keys for scalar/plural overlap. Successful workers end evaluated; reservation failures end failed rather than running or absent.
- Calendar correctness: the Saturday and Sunday New Year cases are independently asserted for daily/minute session output, and the version bump propagates through existing calendar-backed manifest/snapshot evidence.
- Snapshot stability: deterministic tie-breaking occurs before complete-cohort scanning and before the 1,000-component output bound; equal-time cohorts cannot depend on insertion order.
- Compatibility/scope: all 452 Python tests pass; no Swift files, provider payload contracts, Task 4 engine behavior, or ignored ledger files were changed.

### Remaining concerns

- Cohort/component reservation locks are process-local, matching the native app's threaded DuckDB execution model. Cross-process writers remain governed by DuckDB's single-writer deployment constraint.
- The offline XNYS calendar still intentionally models documented repeatable holiday/early-close rules rather than emergency one-off exchange closures; its versioned evidence makes future audited rule upgrades cache-invalidating.
- No credentialed live Binance or Alpaca request was made in this deterministic fix round.
