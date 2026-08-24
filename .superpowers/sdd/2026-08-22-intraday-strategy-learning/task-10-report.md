# Task 10 report

## Fix round 1/1 — final causal-evidence hardening

### Outcome

All nine review findings are fixed and verified. The implementation now fails closed when authenticated revision history, robustness diagnostics, or development-only calibration evidence is unavailable. Nothing in this round claims or guarantees profitability, and no live provider fetch or order action was performed.

### Root causes and fixes

1. **Learning evidence receipt time.** Historical bar timestamps had been reused as run, discovery, evaluation, and persistence receipt timestamps. `LearningExperiment` now separates the authenticated `development_data_through` boundary from actual injected-clock `started_at`; candidate discovery and persisted creation use the actual receipt, while evidence remains bounded by development data. A sealed final receipt never admits a trial evaluated after its boundary. Forward promotion additionally requires observations after actual discovery as well as after the historical evidence boundary.
2. **Decision/outcome shift.** A decision at row `i` produces the return available at row `i+1`, but validation previously selected row `i`. Backtest curves now carry explicit `decision_timestamp` and `outcome_available_at`; validation, fold evidence, development purging, and final selection use this one-to-one map. Fold evaluation is stamped at actual outcome availability.
3. **Equity session context.** Pipeline generators used a default continuous calendar. `StrategyContext.for_market` now selects provider context; Alpaca equity uses an XNYS calendar with New York timezone, holidays, and early closes, while crypto remains continuous.
4. **REST revision fidelity.** Newly retrieved corrected provider bars were incorrectly eligible as if known at their historical close. Bars now persist `source_available_at`, `observed_at`, and `vintage_fidelity`. Binance/Alpaca REST rows are labelled `backfilled_rest_no_revision_history` and become available at retrieval. Schema v3 migrates older rows to `unknown_legacy`. Strict revision-as-of dataset evidence fails closed unless the vintage is authenticated immutable.
5. **Robustness promotion gates.** Promotion did not require all mandated diagnostics. Receipts and gates now cover positive median walk-forward net edge, fold calibration, CSCV/PBO, and parameter-neighborhood stability. Missing evidence rejects promotion. Real fold Brier calibration is calculated from mapped outcomes and causally carries sparse transition signals.
6. **Ensemble economics/calibration.** Probability and economic fields were placeholders. A development-only point-in-time beta-binomial calibrator now derives probability, signed net edge, observed execution cost, uncertainty, receipt time, and a decision-row hash. Final outcomes are excluded. Insufficient or one-class development evidence exposes `calibration_status=unavailable`, and the ensemble abstains rather than inventing confidence.
7. **Python/Swift fixture parity.** Exact diff was impossible because export receipt metadata changes, and plain demo export erased native strategy scenarios. Synchronization now merges deterministic native research sections into the regenerated demo, bounds the native ensemble sample, and compares a canonical semantic hash against the staged fixture. Only receipt metadata (`generated_at`, `last_refresh`, `git_commit`, pipeline run IDs/times, and the export-only run) is normalized. Semantic content changes fail the gate. CI calls this behavioral target.
8. **Swift cancellation escalation.** Cancellation sent only SIGTERM. `EngineRunner` now terminates, waits a bounded 250 ms grace period, then sends SIGKILL if the child is still running. A signal-resistant Python fixture proves the child is gone within the test deadline.
9. **Git-history secret scanning.** The scanner only inspected current tracked files. It now retains that fast scan and additionally enumerates unique blobs reachable from all refs through `git rev-list`/`git cat-file`, scanning without printing secret values. A temporary repository regression commits then deletes a fake secret and still detects it.

### TDD evidence

Each production change began with a real-behavior regression. Representative RED evidence:

- Learning chronology: post-hoc trials were admitted because historical timestamps preceded the final boundary; forward promotion accepted pre-discovery outcomes.
- Boundary mapping: a single decision at the final boundary selected the preceding development return and omitted its own outcome.
- Session scope: Black Friday was treated as a full continuous UTC session and Christmas was not excluded.
- Vintage fidelity: REST bars reported close-time availability and strict revision-as-of evidence remained eligible.
- Robustness/calibration: missing robustness evidence could promote; fold calibration was hard-coded `0.5`; the new focused test initially failed collection with `ImportError: cannot import name 'calculate_fold_calibration_error'`.
- Ensemble: placeholder `0.5 + signal*strength/2` remained actionable without fitted evidence and final-label changes could affect calibration.
- Swift cancellation: the signal-resistant child remained alive after stream cancellation.
- History scanner: a committed-then-deleted credential was not reported.
- Fixture parity: exact export hashes changed only with `metadata.generated_at`; the first full CI merge then correctly exposed Swift's evidence-node limit, leading to a bounded scenario-preserving synchronizer rather than a decoder-limit increase.

Focused GREEN commands/results included:

- `.venv/bin/pytest -q tests/unit/test_strategy_validation.py::test_fold_fitted_decision_calibration_uses_only_development_outcomes tests/unit/test_strategy_validation.py::test_fold_calibration_is_scored_at_mapped_outcome_rows tests/unit/test_strategy_validation.py::test_fold_calibration_causally_carries_sparse_transition_signals tests/integration/test_strategy_cli.py::test_feedback_uses_task4_delayed_fills_and_excludes_outcomes_crossing_the_final_boundary tests/integration/test_strategy_cli.py::test_post_hoc_learning_trials_are_not_admitted_to_the_historical_sealed_boundary` — **5 passed**.
- `.venv/bin/pytest -q tests/unit/test_snapshot_fixture_parity.py` — **2 passed**.
- `swift test --package-path macos/Nowcaster --filter cancellationEscalatesToKillForASignalResistantChild` — passed; the child was killed in about 0.3 seconds.
- `swift test --package-path macos/Nowcaster --filter decodesBundledSchemaV2FixtureAndPreservesLegacySections` — passed after bounded synchronization.

An early broad run identified the expected compatibility surface: **27 failed, 462 passed**. Failures were traced individually; no assertion or production gate was weakened to conceal a causal failure. One pre-existing Swift test measured consumer scheduling time under parallel execution rather than event order; it passed alone but failed in the concurrent suite. It was replaced with the deterministic contract that the decoded `0.5` progress event precedes `job_completed`.

### Final verification

- `.venv/bin/pytest -q` — **493 passed in 280.99s**.
- `.venv/bin/ruff format --check .` — **181 files already formatted**.
- `.venv/bin/ruff check .` — **All checks passed**.
- `git diff --check` — passed.
- `swift test --package-path macos/Nowcaster` — **53 Swift Testing tests plus 1 XCTest passed (54 total)**.
- `swift build -c release --package-path macos/Nowcaster` — production build completed.
- `make verify-research-fixtures` — regenerated schema-v2 CI artifacts and passed byte-drift assertion.
- `make verify-swift-fixture-parity` — semantic parity passed: `c4c3c2a7a7164096b920b19ca725339f75875ed6bf72b09a7635b9a812e8323e`.
- `.venv/bin/python scripts/scan_tracked_secrets.py` — `Tracked-file and reachable-history secret scan passed`.
- No live network fetch was run. Generated DuckDBs and caches remain ignored/outside Git.

Final deterministic research identities:

- code hash: `3b3878bee3b5a8410b47e8f0228d954825e270bda01bc6715af0e14724252a66`
- aggregate dataset hash: `a62830917a55b973413e43658ba45a1713a4db8df311e6fc48219755aaf585c3`
- semantic research snapshot: `0776f21ebe1e7b24c3998fd29dea3173deafbec66df7409e3cf583fdafa56aef`
- CI snapshot file: `8be163614c11e237a55350cdf2cc99389e433fd93a0435a11a7a2e566a30cbd6`

### Files

- Causal learning/promotion: `src/learning/search.py`, `src/learning/promotion.py`, `src/strategies/pipeline.py`.
- Backtest/validation/ensemble: `src/backtest/intraday.py`, `src/strategies/validation.py`, `src/strategies/ensemble.py`.
- Sessions: `src/strategies/calendars.py`, `src/strategies/session.py`, `src/strategies/library.py`.
- Vintage provenance/schema: `src/ingestion/{bars,binance_bars,alpaca_bars}.py`, `src/strategies/datasets.py`, `src/database/{engine,schema}.py`.
- CI/native safety: `.github/workflows/ci.yml`, `Makefile`, `scripts/{synchronize_snapshot_fixture,verify_snapshot_fixture_parity,scan_tracked_secrets}.py`, `EngineRunner.swift`, native fixture and Swift tests.
- Deterministic artifacts: `data/research/ci/*` and the synchronized native snapshot fixture.
- Regressions: affected integration/unit suites plus `tests/unit/test_snapshot_fixture_parity.py`.
- Controller ledger edits in `progress.md` were preserved and included.

### Honest limitations

1. Binance/Alpaca historical REST APIs do not authenticate historical revision vintages. Their backfilled rows therefore cannot support strict revision-as-of claims and remain unavailable for that evidence mode.
2. The real pipeline currently has no complete authenticated CSCV/PBO plus parameter-neighborhood receipt for provider history. Promotion correctly fails closed until those diagnostics are produced; tests exercise valid positive, negative, boundary, and missing cases.
3. Calibration requires at least five development observations and both outcome classes. Sparse or one-sided evidence is explicitly unavailable, so the ensemble abstains.
4. The native fixture intentionally retains a bounded scenario set for UI behavior and security limits; deterministic CI research remains the authoritative complete software fixture.
5. Backtests, fitted calibration, and forward promotion controls reduce leakage and overfitting risk but cannot guarantee future profit. Live execution, market impact, borrow availability, regime change, and provider corrections remain material.
