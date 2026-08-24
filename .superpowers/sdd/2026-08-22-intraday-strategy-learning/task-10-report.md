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

## Exceptional Final Fix Wave 2

### Outcome

The user-authorized four-finding wave is implemented and green. Learning and robustness timestamps now represent injected-clock event boundaries rather than invented ordering time; robustness and economic-cost evidence fail closed unless authenticated; and the bundled Swift research is derived from the authoritative generated Python CI snapshot rather than from itself. The controller's two new `progress.md` ledger entries were preserved.

### Root causes and fixes

1. **Synthetic learning event time.** Candidate discovery reused the run start and trial evaluation/receipt time was `started_at + ordinal microseconds`. That made post-hoc work appear older than a sealed final boundary. `LearningExperiment` now owns an injected event clock. Candidate discovery, trial evaluation, persistence receipt, selected-rule discovery, and selected-rule receipt each call it at their real event boundary. Persisted trials carry authenticated candidate discovery, evaluation, and receipt times. Resumption restores those times and sorts by the explicit ordinal; equal-resolution real timestamps do not affect ordering. Promotion's existing strict post-discovery observation/outcome filter and outer-block consumption tests were retained.
2. **Unreceipted robustness aggregates.** PBO, median net edge, fold calibration, and parameter-neighborhood diagnostics could reach promotion without a boundary/context receipt. `RobustnessEvidence.seal` now hashes the metrics, discovery/evaluation times, development-data-through and sealed-final-start boundaries, cohort, dataset, validation-policy hash, exact fold count, and canonical fold evidence hash. Evaluation accepts it only when it exactly matches admitted development folds and was evaluated before the final boundary. Missing, unsealed, tampered, wrong-cohort, post-hoc, or boundary-equal evidence fails closed.
3. **Economic cost defaulted to zero.** Missing/null `cost_return` was coerced into an optimistic zero-cost calibration. Every mapped outcome now requires finite `cost_return` plus an exactly aligned `cost_decision_timestamp`. The calibrated receipt hash includes both. Failed or unavailable cost evidence produces `economic_evidence_status=unavailable`, and the ensemble explicitly abstains with `economic_cost_evidence_unavailable`; valid modeled-cost evidence remains actionable only when the existing edge/uncertainty gates clear.
4. **Self-referential fixture parity.** The synchronizer previously read the bundled Swift fixture as its own research source, so Python research drift was invisible. `make sync-macos-snapshot` now consumes `data/research/ci/nowcaster-snapshot.json`. The verifier projects the five schema-v2 research sections from the authoritative Python artifact and bundled Swift fixture, applies only the documented native component cap, and compares a deterministic semantic hash. A real CLI regression writes two files, proves the deterministic positive case, mutates Python promotion semantics, and observes a nonzero parity result. Native tests were decoupled from obsolete handcrafted sample values while retaining explicit signed-context presentation scenarios.

### Strict TDD evidence

Production changes followed reproduce/hypothesize/RED/minimal-GREEN cycles:

- Learning RED: the advancing-clock regression failed with `TypeError: LearningExperiment.__init__() got an unexpected keyword argument 'clock'`; persisted evaluation times were still derived from ordinal offsets. GREEN: `.venv/bin/pytest -q tests/unit/test_learning_search.py tests/integration/test_learning_mode.py tests/integration/test_strategy_cli.py -k 'learn or promotion or forward or post_hoc'` — **50 passed, 36 deselected**.
- Robustness RED: `AttributeError: type object 'RobustnessEvidence' has no attribute 'seal'`. GREEN: the positive receipt, post-hoc, tamper, and exact-boundary regression passed; the broader validation/ensemble/engine/backtest suite passed **108 tests**.
- Economic-cost RED: missing, null, NaN, and decision-misaligned costs all returned `evaluated` rather than `failed`. GREEN: the malformed-cost matrix plus mapped intraday outcome suite passed **22 tests**; ensemble actionable, cost-buffer abstention, and unavailable-cost abstention paths also passed in the 108-test focused suite.
- Parity RED: `AttributeError: module 'verify_snapshot_fixture_parity' has no attribute 'research_semantic_hash'`. GREEN: `.venv/bin/pytest -q tests/unit/test_snapshot_fixture_parity.py` — **3 passed**, including positive CLI parity followed by a mutated authoritative Python research failure.
- Native fixture RED after the first authoritative merge: **22 issues** exposed stale handcrafted research-value assumptions. GREEN: scenario-specific values moved into an explicit mutated test fixture, while bundled assertions consume authoritative generated evidence; the complete native suite passed.

No production assertion or evidence gate was weakened to turn RED into GREEN.

### Final verification

- `.venv/bin/pytest -q` — **502 passed in 306.30s**.
- `.venv/bin/ruff format --check .` — **181 files already formatted**.
- `.venv/bin/ruff check .` — **All checks passed**.
- `git diff --check` — passed.
- `make verify-research-fixtures` — schema-v2 artifact validation and deterministic research byte-drift check passed after staging the regenerated authoritative artifacts.
- `make verify-swift-fixture-parity` — Python/Swift research semantic parity passed: `8a1c214792defa8975b5e6f8c19e2c0d221a59e258c9bb3307ad2451407728af`.
- `swift test --package-path macos/Nowcaster` — **53 Swift Testing tests plus 1 XCTest passed (54 total)**.
- `swift build -c release --package-path macos/Nowcaster` — production build completed.
- `.venv/bin/python scripts/scan_tracked_secrets.py` — `Tracked-file and reachable-history secret scan passed`.
- No live network fetch ran. Generated DuckDB files and provider caches remain ignored/outside Git.

Final deterministic research identities:

- code hash: `d975fa6a491bb2d703b619aaa2580dc82e2cb79e6b3408801cfda1626e53589f`
- aggregate dataset hash: `a62830917a55b973413e43658ba45a1713a4db8df311e6fc48219755aaf585c3`
- semantic research snapshot: `82576bf13f6a868bd2084ea86054ea93e3d6c713e96e106d2bcba8be28c6040e`
- authoritative CI snapshot file: `d88ae7f46410f7d9533cfabe20e41aafab1dd8a8dc799a43460454719cff41c7`

### Files changed in wave 2

- Event chronology: `src/learning/search.py`, `src/strategies/pipeline.py`, `tests/unit/test_learning_search.py`.
- Robustness/economics: `src/strategies/validation.py`, `src/strategies/ensemble.py`, `src/backtest/intraday.py`, `tests/unit/test_strategy_validation.py`, `tests/unit/test_strategy_ensemble.py`, `tests/integration/test_strategy_engine.py`.
- Authoritative native parity: `Makefile`, `README.md`, `scripts/verify_snapshot_fixture_parity.py`, `tests/unit/test_snapshot_fixture_parity.py`, generated `data/research/ci/*`, the Swift fixture, and `StrategyLabTests.swift`.
- Evidence ledger/report: `progress.md`, `task-10-report.md`.

### Honest limitations

1. Equal timestamps remain possible when the injected clock has coarse resolution; ordering is therefore the explicit ordinal/sequence, never synthetic time.
2. The real provider pipeline still lacks sufficient authenticated CSCV/PBO and parameter-neighborhood receipts for promotion. This is represented as unavailable and rejects promotion; the sealed positive contract is exercised with exact fold-backed evidence in tests.
3. Modeled cost is not a promise of realized slippage. Missing or malformed modeled cost now abstains, but borrow, market impact, latency, and regime changes remain outside historical certainty.
4. The native app intentionally embeds at most ten ensemble components to respect its bounded decoder contract, but every embedded research section/component is now an exact projection of the authoritative Python artifact and cannot silently diverge.
5. Backtests and causal receipts reduce leakage and audit risk; they do not guarantee profitable day trading.

## Exceptional Fixture-Parity Validation Micro-Fix

### Root cause and correction

The Python/Swift semantic verifier itself compared the correct authoritative sections, but the Make target declared `sync-macos-snapshot` as a prerequisite. CI therefore rewrote the checked-in Swift fixture from Python immediately before comparing it. A stale committed fixture silently healed and passed, and even an already matching fixture changed bytes because synchronization rewrote export formatting/receipt content.

`verify-swift-fixture-parity` is now a prerequisite-free, read-only operation. It compares the checked-in Swift fixture directly with `data/research/ci/nowcaster-snapshot.json`. `sync-macos-snapshot` remains a separate explicit developer maintenance command, documented independently in the README. CI already invokes the verification target, so its existing path now fails on stale committed semantics without mutation.

### TDD evidence

The regression invokes the actual repository Make target from a controlled temporary workspace. A deterministic fake export executable permits the old synchronization prerequisite to run without network or production data. It exercises both matching and divergent Python/Swift inputs and hashes the Swift bytes around the target.

- RED: `.venv/bin/pytest -q tests/unit/test_snapshot_fixture_parity.py::test_make_parity_target_is_read_only_for_matching_and_divergent_fixtures` failed because the matching Swift file was reformatted/rewritten before comparison (`assert swift_path.read_bytes() == matching_bytes` failed). Under the old prerequisite, the divergent fixture was likewise overwritten from Python and the target returned success.
- Minimal GREEN: removed only the synchronization prerequisite from the verification target. The explicit sync recipe was retained unchanged.
- Focused GREEN: the Make-facing regression — **1 passed in 0.41s**; `.venv/bin/pytest -q tests/unit/test_snapshot_fixture_parity.py` — **4 passed in 0.42s**; `make verify-swift-fixture-parity` — passed with hash `8a1c214792defa8975b5e6f8c19e2c0d221a59e258c9bb3307ad2451407728af`.

### Final verification

- `.venv/bin/pytest -q` — **503 passed in 298.80s**.
- `.venv/bin/ruff format --check .` — **181 files already formatted**.
- `.venv/bin/ruff check .` — **All checks passed**.
- `swift test --package-path macos/Nowcaster` — **53 Swift Testing tests plus 1 XCTest passed (54 total)**.
- `swift build -c release --package-path macos/Nowcaster` — production build completed.
- `make verify-research-fixtures` — regenerated schema-v2 research and passed the staged byte-drift assertion.
- `make verify-swift-fixture-parity` — read-only authoritative parity passed with hash `8a1c214792defa8975b5e6f8c19e2c0d221a59e258c9bb3307ad2451407728af`.
- `.venv/bin/python scripts/scan_tracked_secrets.py` — current and reachable-history scan passed.
- `git diff --check` and staged diff check — passed.
- No live network fetch ran; raw caches and generated DuckDBs remain ignored/outside Git.

The Makefile change updates the deterministic research code hash to `b05fd784633a4eebbd10110d9a44dd2645fdb0996c6a6faf6bfbf376c81aa6b1`; the semantic research snapshot remains `82576bf13f6a868bd2084ea86054ea93e3d6c713e96e106d2bcba8be28c6040e`.

### Files and concerns

- Changed: `Makefile`, `README.md`, `tests/unit/test_snapshot_fixture_parity.py`, deterministic research summary/report, controller `progress.md`, and this report.
- The Swift fixture did not require regeneration because its authoritative research semantics already match. Explicit synchronization still updates volatile demo receipt bytes by design; verification never invokes it.
- The parity gate covers schema version and the five native research sections, including the documented ten-component native projection. It intentionally does not claim byte identity for unrelated demo/export receipt sections.

## Fresh Native Visual Audit

The release candidate was assembled with `make macos-app`, validated with `codesign --verify --deep --strict`, and captured with `make macos-screenshots`. The capture tool launched real native windows and asserted their dimensions before saving 22 current-run PNGs: every primary destination in light and dark appearance, plus representative 900×700 narrow layouts. `make macos-ui-test` also passed against a live Strategy Lab window.

1. **Today / task entry — healthy.** The research briefing, explicit long/short research labels, calibration state, and no-assurance copy are legible without depending on color.
2. **Strategy Lab wide — healthy.** Three-column hierarchy, research-only/no-broker disclosure, bounded budget control, strategy/posture table, evidence status, separate development/final evidence, chart-data alternative, and coverage provenance are visible in light and dark appearance.
3. **Strategy Lab narrow — healthy.** At 900×700, navigation, strategy selection, learning progress, evidence cards, and scrolling remain usable; no cropped toolbar or duplicated wide capture was observed.
4. **Affected research/system screens — healthy.** Backtests clearly separate development from final test and show `Not ready`; Signals identifies `Research Only` and invalidation evidence; Data Quality has a descriptive empty state; Pipeline Runs exposes status, stage, mode, duration, rows, and errors.

No release-blocking visual, interaction, or screenshot-visible accessibility defect was found. Screenshot evidence cannot prove full keyboard, VoiceOver, contrast-ratio, Dynamic Type, or reduced-motion compliance; the native test suite separately covers stable accessibility labels, non-color status descriptions, keyboard-oriented selection/search contracts, chart table alternatives, and window resizing.
