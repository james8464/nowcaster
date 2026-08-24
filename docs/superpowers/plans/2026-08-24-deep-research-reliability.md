# Deep Research Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a resumable, parallel, trial-aware Deep Research engine and native macOS workflow that searches for robust day-trading challengers without leaking sealed or forward evidence or weakening broker safety.

**Architecture:** A coordinator freezes the dataset and protocol, writes an append-only experiment ledger, dispatches pure fold evaluations to worker processes, and applies hard statistical/stress gates before comparing a challenger with the incumbent research champion. The existing Strategy Pipeline remains the source of authenticated bars and causal simulation; schema-v5 projections expose results to a SwiftUI control surface that starts, pauses, resumes, and stops local research.

**Tech Stack:** Python 3.11+, pandas, NumPy, SciPy, SQLAlchemy/SQLite, Typer, Pydantic, `concurrent.futures.ProcessPoolExecutor`, Swift 6, SwiftUI, Swift Charts, Swift Testing/XCTest.

**Spec:** `docs/superpowers/specs/2026-08-24-deep-research-reliability-design.md`

## Global Constraints

- Deep Research is research-only and cannot submit broker orders or arm live execution.
- The sealed final test is selected before filtering and consumed once per immutable candidate version.
- Every generated candidate attempt is counted, including duplicate, invalid, failed, interrupted, and unprofitable attempts.
- Fixed inputs must produce deterministic results independent of worker completion order.
- Resource-intensive work uses processes with numeric-library threads fixed to one.
- Missing or incompatible data fails closed with an explicit reason.
- Existing schema-v4 snapshots remain rejected by schema-v5 clients only through the established compatibility message; fixtures and exporters advance together.
- All production behavior is introduced test-first.

---

### Task 1: Deep Research Contracts and Schema v5 Ledger

**Files:**
- Create: `src/deep_research/contracts.py`
- Create: `src/deep_research/repository.py`
- Create: `src/deep_research/__init__.py`
- Modify: `src/database/schema.py`
- Test: `tests/unit/test_deep_research_contracts.py`
- Test: `tests/integration/test_deep_research_repository.py`

**Interfaces:**
- Consumes: `src.strategies.types.canonical_hash`, `src.database.engine.Database`.
- Produces: `ResearchProtocol`, `ResearchRun`, `CandidateAttempt`, `FoldEvidence`, `StressEvidence`, `PromotionEvidence`, `ResourceSample`, and `DeepResearchRepository` append/read/checkpoint methods.

- [ ] **Step 1: Write failing contract tests** proving UTC-only timestamps, bounded workers/budgets, immutable hashes, finite metrics, allowed run states, and deterministic canonical protocol identity.
- [ ] **Step 2: Run** `.venv/bin/python -m pytest tests/unit/test_deep_research_contracts.py -v` and confirm imports fail because `src.deep_research` does not exist.
- [ ] **Step 3: Implement typed frozen dataclasses** with `ResearchProtocol.identity` derived from dataset/code/search/cost/protocol inputs and explicit `RunState`/`AttemptStatus` enums.
- [ ] **Step 4: Run the contract tests** and confirm they pass.
- [ ] **Step 5: Write failing repository tests** that initialize schema v5, append all attempt statuses, reject ordinal overwrite, persist out-of-order completions in ordinal order, checkpoint, resume matching hashes, and reject mismatched hashes.
- [ ] **Step 6: Add schema-v5 tables** `deep_research_runs`, `deep_research_trials`, `deep_research_fold_metrics`, `deep_research_stress_metrics`, `deep_research_promotions`, `deep_research_checkpoints`, and `deep_research_resource_samples`; add every table to schema validation and deterministic natural-key definitions.
- [ ] **Step 7: Implement the single-writer repository** using transactions and insert-only evidence rows; expose `create_run`, `append_attempt`, `append_fold_evidence`, `append_stress_evidence`, `checkpoint`, `set_state`, and `load_resume_state`.
- [ ] **Step 8: Run** `.venv/bin/python -m pytest tests/unit/test_deep_research_contracts.py tests/integration/test_deep_research_repository.py -v`.
- [ ] **Step 9: Commit** with `feat: add deep research evidence ledger`.

### Task 2: Trial-Aware Reliability Statistics and Promotion Gates

**Files:**
- Create: `src/deep_research/statistics.py`
- Create: `src/deep_research/promotion.py`
- Test: `tests/unit/test_deep_research_statistics.py`
- Test: `tests/unit/test_deep_research_promotion.py`

**Interfaces:**
- Consumes: per-trial returns/fold metrics plus immutable total trial count.
- Produces: `deflated_sharpe_probability`, `bootstrap_positive_edge_probability`, `probability_of_backtest_overfitting`, `parameter_stability`, `ReliabilityThresholds`, and `evaluate_research_promotion`.

- [ ] **Step 1: Write failing deterministic numerical tests** using seeded synthetic profitable, noise, concentrated-profit, unstable-neighbor, and too-few-trade series.
- [ ] **Step 2: Run the two unit files** and verify missing-module failures.
- [ ] **Step 3: Implement finite fail-closed statistics** using SciPy distribution functions, CSCV-style train/test rank reversals, seeded block bootstrap, and clipped `[0, 1]` probabilities.
- [ ] **Step 4: Implement default gates exactly:** 300 trades, positive median fold net return and Sharpe, positive doubled-cost return, DSR and positive-edge probability `>= 0.99`, PBO `<= 0.10`, stability `>= 0.80`, drawdown `<= 0.10`, concentration `< 0.50`, positive sealed result, all audits true, and material incumbent improvement.
- [ ] **Step 5: Verify every undefined/NaN/inf statistic produces a failed decision with a named reason.**
- [ ] **Step 6: Run** `.venv/bin/python -m pytest tests/unit/test_deep_research_statistics.py tests/unit/test_deep_research_promotion.py -v`.
- [ ] **Step 7: Commit** with `feat: add trial-aware reliability gates`.

### Task 3: Stress Simulation and Bounded Candidate Generation

**Files:**
- Create: `src/deep_research/candidates.py`
- Create: `src/deep_research/stress.py`
- Modify: `src/learning/grammar.py`
- Test: `tests/unit/test_deep_research_candidates.py`
- Test: `tests/unit/test_deep_research_stress.py`

**Interfaces:**
- Consumes: existing `RuleNode`, registered parameter schemas, return/fill series, and a pre-registered seed.
- Produces: deterministic `CandidateDefinition` streams and `StressReport` with baseline, doubled/severe cost, delayed-fill, reduced-liquidity, skipped-winner, clustered-loss, parameter-neighbor, alternate-start, and block-bootstrap scenarios.

- [ ] **Step 1: Write failing candidate tests** for seeded baselines, stratified parameters, mutation/crossover bounds, semantic deduplication, explicit duplicate attempts, no arbitrary-code nodes, and stable ordinals.
- [ ] **Step 2: Run candidate tests** and confirm the new interfaces are absent.
- [ ] **Step 3: Implement a closed candidate generator** that wraps the existing grammar and parameter schemas; candidates contain data only and validate maximum depth/nodes/lag/risk bounds.
- [ ] **Step 4: Write failing stress tests** proving deterministic block bootstraps preserve series length, each required scenario exists, best-trade removal cannot improve reported profit, and absent liquidity lowers evidence grade.
- [ ] **Step 5: Implement stress evaluation** with seeded stationary blocks and explicit conservative transforms; return finite metrics or a failed scenario.
- [ ] **Step 6: Run** `.venv/bin/python -m pytest tests/unit/test_deep_research_candidates.py tests/unit/test_deep_research_stress.py -v`.
- [ ] **Step 7: Commit** with `feat: add bounded challengers and stress simulation`.

### Task 4: Deterministic Parallel Coordinator and Controls

**Files:**
- Create: `src/deep_research/worker.py`
- Create: `src/deep_research/control.py`
- Create: `src/deep_research/coordinator.py`
- Test: `tests/unit/test_deep_research_control.py`
- Test: `tests/integration/test_deep_research_coordinator.py`

**Interfaces:**
- Consumes: `ResearchProtocol`, authenticated development/fold frames, `DeepResearchRepository`, candidate generator, stress evaluator, and an event sink.
- Produces: `DeepResearchCoordinator.run() -> DeepResearchOutcome`, atomic `ResearchControl` pause/resume/stop state, deterministic checkpoints, and newline-compatible progress payloads.

- [ ] **Step 1: Write failing control tests** for atomic ownership, invalid transitions, pause draining, resume, stop, and corrupt-command rejection.
- [ ] **Step 2: Implement run-scoped JSON control files** written atomically with nonce and run identity; broker actions are not representable.
- [ ] **Step 3: Write failing coordinator tests** for worker counts, out-of-order completion, fixed-seed determinism across one/four workers, one retry after crash, persistent repeat failure, finite-cycle continuous mode, checkpoint/resume, stop preservation, and numeric thread limits.
- [ ] **Step 4: Implement pure worker payloads** and a bounded `ProcessPoolExecutor`; the coordinator buffers completions and commits by ordinal.
- [ ] **Step 5: Implement cycle orchestration** for candidate generation, inner-fold evaluation, shortlist stress, single-use sealed evaluation, promotion comparison, progress, and safe checkpoint termination.
- [ ] **Step 6: Prove no coordinator or control module imports `src.trading.alpaca`, `BrokerAdapter`, `LiveBrokerFactory`, or order submission interfaces.**
- [ ] **Step 7: Run** `.venv/bin/python -m pytest tests/unit/test_deep_research_control.py tests/integration/test_deep_research_coordinator.py -v`.
- [ ] **Step 8: Commit** with `feat: run resumable parallel deep research`.

### Task 5: Strategy Pipeline, CLI, and Data-Coverage Integration

**Files:**
- Modify: `src/strategies/pipeline.py`
- Modify: `src/cli.py`
- Modify: `src/config/settings.py`
- Modify: `config/strategies.yaml`
- Test: `tests/integration/test_deep_research_cli.py`
- Test: `tests/integration/test_deep_research_pipeline.py`

**Interfaces:**
- Consumes: authenticated `_authenticated_coverage`, causal bar repository, raw final boundary, registered strategies, and `DeepResearchCoordinator`.
- Produces: `DeepResearchOptions`, `StrategyPipeline.deep_research`, and `strategy deep-research` CLI with workers, budget/continuous, time budget, seed, resume, and control directory.

- [ ] **Step 1: Write failing pipeline tests** for complete manifest reuse, incomplete coverage refusal, provider separation, final-boundary freezing before filtering, point-in-time bars, and explicit unavailable outcomes.
- [ ] **Step 2: Write failing CLI tests** for bounded and continuous invocations, invalid worker/budget combinations, resume identity mismatch, progress JSON, and absence of broker arguments.
- [ ] **Step 3: Implement validated options/config defaults** and adapt authenticated bars into the coordinator without duplicating ingestion or execution logic.
- [ ] **Step 4: Add the Typer command** and ensure cancellation returns a successful stopped outcome while corrupt evidence returns nonzero.
- [ ] **Step 5: Run** `.venv/bin/python -m pytest tests/integration/test_deep_research_cli.py tests/integration/test_deep_research_pipeline.py -v`.
- [ ] **Step 6: Commit** with `feat: expose deep research pipeline`.

### Task 6: Snapshot Schema v5 and Research Reporting

**Files:**
- Modify: `src/app_snapshot/models.py`
- Modify: `src/app_snapshot/builder.py`
- Modify: `src/reporting/strategy_report.py`
- Modify: `scripts/synchronize_snapshot_fixture.py`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Models/Snapshot.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Resources/Fixtures/nowcaster-snapshot.json`
- Test: `tests/unit/test_deep_research_snapshot.py`
- Test: `tests/integration/test_app_snapshot_export.py`
- Test: `macos/Nowcaster/Tests/NowcasterAppTests/SnapshotDecodingTests.swift`

**Interfaces:**
- Consumes: append-only deep-research tables.
- Produces: schema-v5 `deepResearchRuns`, trial/stage/resource summaries, champion comparison, gate results, coverage posture, and honest outcome enum.

- [ ] **Step 1: Write failing Python projection tests** for active/stopped/completed runs, no-reliable-strategy outcome, champion-found outcome, evidence separation, bounded payloads, and malformed-row fail-closed behavior.
- [ ] **Step 2: Implement Pydantic models and builder projections** using aggregate queries so snapshots do not contain unbounded trial returns or secret/control paths.
- [ ] **Step 3: Update the report** to label simulations, list all failed gates, trial count, data coverage, and forward-testing requirement.
- [ ] **Step 4: Write failing Swift decoding/validation tests** for schema v5 and malformed probability/resource bounds.
- [ ] **Step 5: Add matching Swift DTOs and validation; regenerate the fixture through the synchronizer.**
- [ ] **Step 6: Run Python snapshot tests and** `cd macos/Nowcaster && swift test --filter SnapshotDecodingTests`.
- [ ] **Step 7: Commit** with `feat: publish deep research evidence`.

### Task 7: Native SwiftUI Deep Research Workspace

**Files:**
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Models/EngineJob.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Services/EngineRunner.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/AppModel.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/StrategyLabView.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/LearningWorkspaceView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/DeepResearchConfigurationView.swift`
- Create: `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/DeepResearchWorkspaceView.swift`
- Test: `macos/Nowcaster/Tests/NowcasterAppTests/DeepResearchTests.swift`
- Test: `macos/Nowcaster/Tests/NowcasterAppTests/EngineRunnerTests.swift`

**Interfaces:**
- Consumes: schema-v5 DTOs, engine progress events, `ProcessInfo.activeProcessorCount`, thermal and power notifications.
- Produces: `EngineJob.deepResearch`, typed resource profile/configuration, Start/Pause/Resume/Stop UI, thermal presentation, champion/challenger evidence cards, and three honest completion outcomes.

- [ ] **Step 1: Write failing Swift tests** for safe defaults, worker clamping, Performance/Balance/Custom invocations, continuous budgets, control commands, progress decoding, and secret-free arguments.
- [ ] **Step 2: Run** `cd macos/Nowcaster && swift test --filter DeepResearchTests` and verify failures.
- [ ] **Step 3: Implement typed jobs and AppModel lifecycle** with process termination fallback, checkpoint-aware controls, low-power Balanced default, and thermal critical pause.
- [ ] **Step 4: Write failing presentation tests** for all outcome labels, hypothetical-performance disclosure, data gaps, gate failures, resource status, accessibility labels, and research-only copy.
- [ ] **Step 5: Build native SwiftUI views** with standard sheets, pickers, gauges/progress, tables, semantic colors, keyboard shortcuts, VoiceOver labels, and reduced-motion behavior.
- [ ] **Step 6: Run** `cd macos/Nowcaster && swift test --filter DeepResearchTests`.
- [ ] **Step 7: Build the app** with `scripts/build_macos_app.sh` and inspect Strategy Lab at compact and regular window sizes using the existing capture workflow.
- [ ] **Step 8: Commit** with `feat: add native deep research workspace`.

### Task 8: Documentation, Packaging, Full Verification, and Publication

**Files:**
- Modify: `README.md`
- Modify: `docs/strategy-methodology.md`
- Modify: `docs/backtest_protocol.md`
- Modify: `docs/macos_app.md`
- Modify: `docs/privacy.md`
- Modify: `Makefile`
- Modify: `scripts/build_engine_bundle.sh`
- Modify: `.github/workflows/release.yml`
- Test: `tests/integration/test_deep_research_end_to_end.py`

**Interfaces:**
- Consumes: complete Deep Research engine and UI.
- Produces: beginner operating guidance, reproducible verification command, bundled dependencies, CI coverage, and published main branch.

- [ ] **Step 1: Write a failing end-to-end test** that builds authenticated fixture coverage, runs parallel search, records every attempt, produces either a gate-complete champion or explicit no-reliable-strategy result, exports schema v5, resumes deterministically, and proves broker ledgers remain untouched.
- [ ] **Step 2: Implement packaging and CI changes** so the helper contains all deep-research modules and the release workflow runs the end-to-end gate.
- [ ] **Step 3: Update beginner documentation** explaining Start Deep Research, resource usage, why more trials increase false discovery risk, how abstention works, and why backtests cannot guarantee money.
- [ ] **Step 4: Run the focused end-to-end test**, then the complete Python suite, Ruff format/check, complete Swift suite, engine bundle build, manifest verification, SBOM generation, macOS app build, `codesign --verify --deep --strict`, secret/history scan, and production-release verifier.
- [ ] **Step 5: Review the implementation against every acceptance criterion in the specification and resolve every unsupported claim or missing test.**
- [ ] **Step 6: Commit final documentation/verification updates, push `main` to `origin`, and verify local and remote commit hashes match.**
