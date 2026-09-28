# Simple Desk and Background Paper Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a simpler native Mac trading-research interface with explicitly started, resource-limited background learning that retains its evidence and never silently changes an active strategy.

**Architecture:** A single app-owned coordinator supervises the paper collector and a separate research worker, independent of windows. A durable learning registry schedules bounded training batches and records final-test exposure before evaluation. Native views consume shared, freshness-checked presentation models; existing research and trading gates remain authoritative.

**Tech Stack:** Existing SwiftUI/AppKit, Observation, XCTest/Swift Testing, Python 3.13, Pydantic, existing causal replay/candidate grammar, existing signed engine helper. No new runtime dependency or daemon.

**Spec:** `docs/superpowers/specs/2026-09-28-simple-desk-background-research-design.md` (user approved).

**Execution:** User approved this written plan on 28 September 2026. Preserve the user's earlier Subagent-driven preference. Tasks 1–6 are sequential integration checkpoints, not permission to start six competing implementations.

## Global Constraints

- Keep SwiftUI/AppKit, the installed `.app`, Dock presence, and the existing branch.
- No broker connection, credentials, orders, automatic notification opt-in, or new OS daemon.
- Never touch the frozen September 8 study, its checkout/environment, protocol, campaigns, records, positions, gaps, or losses.
- Separate new research from both that study and the currently registered paper-desk protocol.
- Primary navigation: **Trade Desk, Markets, History, Research**; preserve all existing advanced destinations.
- The current desk covers BTC/USDT and ETH/USDT Binance spot, long/stand-aside only.
- Default to at most one new scheduled batch per asset per UTC day, a maximum of 100 candidate attempts per batch, and require a new eligible data fingerprint.
- A final holdout is evaluated once for a locked candidate; after inspection, it cannot qualify later tuned candidates as unseen.
- A research winner is a proposal for a new, separately registered prospective paper evaluation, never a silent replacement of an active/frozen strategy.
- Default to efficient background work: half the logical cores, reserving at least two where available, with one numerical thread per worker.
- Pause dispatch on serious/critical/unknown thermal state, low-power mode, memory/disk pressure or an unhealthy live collector.
- Resume automatically only after a resource-triggered pause recovers, not after a user pause.
- Do not change system sleep settings: no local collection/computation occurs while the Mac is asleep or offline, and missed intervals stay recorded as gaps.
- Keep all changes on `feature/research-round-2`; preserve user work. Current baseline is `bfc5dfc9c01c4f0e775f812ea114e9c80a63e164`; its CI 36413346806 succeeded.
- Preserve deployment floors and existing dependency pins; use availability checks instead of requiring a newer OS. Do not repeat completed historical strategy searches.
- Read applicable HIG pages before changing their controls; record verified/issues/not-applicable/not-tested, never blanket certification.

## Review Focus

1. A crash during append or a second process opening the same campaign must preserve evidence and block inconsistent state, not recreate a clean ledger (Task 1).
2. Clock rollback, a new dataset hash containing old holdout dates, or delayed/revised observations must not reset budgets or make exposed/future data unseen (Tasks 1–2).
3. Closing/reopening two windows and rapidly alternating Start/Pause must not create duplicate children or restart a user-paused session (Task 4).
4. A worker that fails to checkpoint or exit must not stall Quit indefinitely or leave compute children running; unrelated collectors must survive (Tasks 3–4).
5. An asset detail already open when its evidence expires must immediately lose actionable levels without hiding historical records (Task 5).

## File boundaries and sequencing

Paths below are relative to the existing `.worktrees/research-round-2` checkout. New Python code lives in `src/background_research/`; new native coordinator/services in `macos/Nowcaster/Sources/NowcasterApp/Services/`; new everyday views in `Features/TradeDesk/`. Do not enlarge `AppModel.swift` into the research engine. The Xcode project already uses filesystem-synchronized source/test groups.

Each task follows RED → minimal implementation → GREEN → commit. Run affected suites during development and the entire changed-language suite at the task checkpoint. Retain failed results. Do not regenerate historical results to obtain a passing research outcome; regenerate only source-identity fixtures with semantic parity checks when required by code changes.

### Task 1: Immutable learning campaigns, budgets and exposure registry

**Files:** Create `src/background_research/__init__.py`, `contracts.py`, `registry.py`, `scheduler.py`; create `tests/unit/test_background_research_registry.py`, `tests/unit/test_background_research_scheduler.py`, and `tests/background_research_fixtures.py`.

**Interfaces:**
- `LearningCampaign`: frozen Pydantic model with `campaign_id`, `source_directory`, `source_protocol_hash`, `code_hash`, `symbols`, `search_spaces`, `cost_policy`, `schedule`, `seed`, `max_attempts_per_batch=100`, `max_batches_per_asset_day=1`, `created_at`; canonical `identity_hash`. Schedule/cost/coverage/trade-count constraints are copied from the registered source protocol, never weakened.
- `LearningBatch`: frozen model with `batch_id`, `campaign_hash`, `symbol`, `utc_day`, `data_fingerprint`, `training_start`, `training_end`, `validation_end`, `holdout_end`, `max_attempts`, `created_at`.
- `LearningStatus`: versioned strict JSON model with campaign/batch identities, `state`, `reason`, attempt/failure counts, last checkpoint, next eligible time and `paper_only=True`; states `idle`, `training`, `waiting`, `pausing`, `paused`, `blocked`, `completed`, `failed`.
- `LearningRegistry(root: Path)`: `register(campaign) -> None`, `reserve_batch(batch) -> bool`, `append_event(batch_id: str, event: dict) -> None`, `reserve_holdout(batch_id: str, candidate_hash: str) -> str`, `read_status(campaign_hash: str) -> LearningStatus`.
- `LearningScheduler(registry: LearningRegistry).next_batch(campaign: LearningCampaign, *, symbol: str, data_fingerprint: str, through: datetime, now: datetime) -> LearningBatch | None`.

- [ ] **Write failing tests**, with fixture builder `campaign_fixture(tmp_path, *, campaign_id="test", seed=0) -> LearningCampaign` defined in `tests/background_research_fixtures.py`. It uses a separate test directory and literal 90/30/30-day schedule, 0.995 coverage and 100-trade minimum, not live files. Assertions include:

```python
def test_same_asset_day_and_fingerprint_cannot_buy_another_batch(registry, batch):
    assert registry.reserve_batch(batch) is True
    assert registry.reserve_batch(batch) is False
    assert registry.reserve_batch(batch.model_copy(update={"batch_id": "other"})) is False

def test_holdout_exposure_survives_campaign_and_dataset_rename(registry, batch, overlapping_batch):
    registry.reserve_batch(batch)
    registry.reserve_holdout(batch.batch_id, "a" * 64)
    registry.reserve_batch(overlapping_batch)
    with pytest.raises(ValueError, match="exposed"):
        registry.reserve_holdout(overlapping_batch.batch_id, "b" * 64)
```

Also test negative/bool budgets, non-UTC times, immutable registration, concurrent reservations, torn trailing JSON, fsync failure, clock rollback, unchanged fingerprint on a later day, symlink into protected study directories, and reentry after a crash. Each blocked operation leaves original file bytes intact.
- [ ] **Run RED:** `python -m pytest tests/unit/test_background_research_registry.py tests/unit/test_background_research_scheduler.py -q`; verify missing new contracts, not malformed fixtures, cause failure.
- [ ] **Implement registry and scheduler.** Store new state under `~/Library/Application Support/Nowcaster/BackgroundResearch` at runtime, never in a source study directory. Reuse `jsonl_writer_lock` and `append_jsonl_fsync` from `round_two_registry.py`. Use a single root-wide registry lock; append immutable events and atomically publish derived status. A malformed ledger blocks without repair/reset. Holdout exposure is indexed by provider/feed/symbol/interval and overlapping UTC ranges, not merely campaign/dataset hashes. Reserve exposure before reading/evaluating held-out outcomes; an interrupted evaluation consumes it. Keep trial counts across campaigns, including rejected/invalid/failed candidates.
- [ ] **Verify GREEN** with the same command, then `python -m pytest -q`; record real failures. Ensure reading an existing source protocol does not mutate it.
- [ ] **Commit** only Task 1 files: `feat: retain bounded learning campaigns and test exposure`.

### Task 2: Causal training adapter and one-shot evaluation

**Files:** Create `src/background_research/data.py`, `training.py`; modify `src/deep_research/coordinator.py` to expose a training-only evaluation path and `src/strategies/pipeline.py` to use it for continuous search; create `tests/unit/test_background_research_data.py`, `tests/integration/test_background_research_training.py`; extend `tests/integration/test_deep_research_coordinator.py` and `test_deep_research_pipeline.py`.

**Interfaces:**
- `EligibleLearningData`: dataclass carrying immutable observation identities, eligible fingerprint, retained quality/gaps, training/validation/final boundaries and separate phase data. No phase may claim earlier availability than retained receipts.
- `load_learning_data(campaign: LearningCampaign, batch: LearningBatch, *, now: datetime) -> EligibleLearningData` reads source `RoundObservation` records without writing to the source.
- Extend `DeepResearchCoordinator.run(..., evaluate_final: bool = True)`; `evaluate_final=False` returns training results/checkpoints and must never call `sealed_evaluator`, `_promotion`, or append a promotion. Reject `evaluate_final=True` for a continuous protocol. Update the existing continuous pipeline to pass `False` and label its output training-only; bounded callers retain their behavior. Background code always passes `False` and uses the new registry for separate one-shot evaluation.
- `LearningTrainer(registry: LearningRegistry).run_batch(campaign: LearningCampaign, batch: LearningBatch, *, control: ResearchControl, emit: Callable[[dict], None]) -> LearningStatus`.

- [ ] **Write RED tests** with deterministic timestamped bars and arrival receipts, including a late revision and an extreme future price. Mutating any observation unavailable at a decision must leave every earlier decision/trade unchanged. A `sealed_evaluator` sentinel raising `AssertionError` must never be called by training-only mode. Freeze the literal training winner before validation/final evaluation; changed test returns must not alter that selection.

```python
def test_training_only_never_promotes_or_reads_final(coordinator, works, repository):
    result = coordinator.run(works, evaluate_final=False)
    assert result.promotion_outcome == "training_only"
    assert repository.promotion_count() == 0
```

Use a test repository adapter for the count assertion; it inspects retained rows rather than mocking a promotion response. Test existing continuous-pipeline generations without final-test reads, one-shot evaluation after crash, overlapping ranges under another campaign, no qualified result with fewer than 100 trades/0.995 coverage, all losing candidates retained, costs/stress included, and source ledgers unchanged.
- [ ] **Run RED:** `python -m pytest tests/unit/test_background_research_data.py tests/integration/test_background_research_training.py tests/integration/test_deep_research_coordinator.py -q`.
- [ ] **Implement** using `CandidateSearchSpace`, `generate_candidates`, `CandidateEvaluationPayload`, the existing next-observation replay and conservative cost model. Capture an immutable source prefix for each batch; use retained `available_at`, not downloaded timestamps as retrospective availability. Filter training indicator inputs to training only, including warmup. Cache only protocol/source/phase-bound immutable results.
- [ ] **Implement selection and evaluation:** spend at most 100 attempts, with two deterministic generations (50 then up to 50 training-only mutations of the best training candidate); duplicates/invalid attempts consume budget and remain recorded. Use a continuous training-only coordinator protocol with cycle budget 50, bounded by the external immutable batch's total 100-attempt budget. Use existing worker fitness and canonical-hash tie breaking. Lock one selection before validation; apply existing gates and global trial accounting. Only if it passes, reserve a previously unexposed final interval and evaluate once. Daily training may continue, but exhausted held-out data yields `waiting`, not repeated qualification. Emit a proposal record for separate prospective research; never call live promotion/notification functions. A proposal includes gross/net results, trade count, drawdown, costs and failed gates, not only its rank.
- [ ] **Verify GREEN** with the focused command and full Python suite. Run causal prefix tests on real replay code, not only synthetic lists of returns.
- [ ] **Commit:** `feat: isolate adaptive training from one-shot final evaluation`.

### Task 3: Checkpointable worker, process ownership and packaged commands

**Files:** Create `src/background_research/runtime.py`; modify `src/cli.py`, `scripts/build_engine_bundle.sh`, and `tests/unit/test_engine_packaging.py`; create `tests/integration/test_background_research_runtime.py`, `test_background_research_cli.py`.

**Interfaces:**
- `BackgroundLearningRunner(registry: LearningRegistry, trainer: LearningTrainer).run(campaign_hash: str, *, control: ResearchControl, emit: Callable[[dict], None]) -> LearningStatus`.
- Existing engine CLI gains `strategy background-research --registry-directory PATH --campaign-hash HASH --run-id ID --control-directory PATH --control-nonce NONCE` and `strategy register-background-research --registry-directory PATH --manifest PATH`. Registration never initializes or rewrites the selected source desk.
- Emit existing JSON progress framing plus versioned `LearningStatus`; first event is an ownership handshake containing run identity, nonce, child PID and isolated process-group ID. No secrets or raw account data in events.

- [ ] **Write RED subprocess tests**: real CLI registration and bounded run, identical resume identity/trial prefix, second worker denied, nonce mismatch denied, insufficient source history waits without repeated batches, stop while queued/in-flight, crash preserves interrupted attempts, corrupt checkpoint blocks, broken pipe cannot erase results. A timed-out worker and its compute children exit, while an unrelated test process remains alive.
- [ ] **Run RED:** `python -m pytest tests/integration/test_background_research_runtime.py tests/integration/test_background_research_cli.py tests/unit/test_engine_packaging.py -q`.
- [ ] **Implement worker lifecycle.** One selected batch at a time; reuse numeric-thread limits, memory/disk guards and control primitives. Wait on bounded control/source-change checks (minimum 30-second idle backoff, never a tight search loop). Stop requests checkpoint completed attempts, record in-flight interruption and join workers. Resume the same batch and attempt identities without resetting evidence. Registry/status write failures stop dispatch and return a visible error.
- [ ] **Implement process isolation:** create a new process group in the worker before spawning children; retain run/nonce/PID ownership. TERM requests orderly stop. Native timeout cleanup may signal only a still-owned isolated group, never process-name matching, a reused PID, an external monitor, or the frozen study. Integration tests verify group cleanup and ordinary checkpoint success separately.
- [ ] **Package and verify** commands in the existing engine helper, not a third app or daemon. Exclude provider credentials from the background command environment. Run focused tests and full Python suite; build the helper and run the marked synthetic CLI fixture from the packaged executable. Check signature and unchanged source fixture prefix.
- [ ] **Commit:** `feat: package checkpointable background research worker`.

### Task 4: One native session owner, resources, resume and Quit

**Files:** Create `Services/PaperSessionCoordinator.swift`, `Services/BackgroundResearchService.swift`, `Services/PaperSessionPreferences.swift`, `Services/BackgroundResourceMonitor.swift`, `Models/BackgroundResearchModels.swift` under `macos/Nowcaster/Sources/NowcasterApp/`; modify `Services/LivePaperSignalService.swift`, `Services/EngineRunner.swift`, `Models/EngineJob.swift`, `AppModel.swift`, `NowcasterApp.swift`, `RootView.swift`; create corresponding `PaperSessionCoordinatorTests.swift`, `BackgroundResearchServiceTests.swift`, `PaperSessionPreferencesTests.swift` in `macos/Nowcaster/Tests/NowcasterAppTests/`.

**Interfaces:**
- `PaperSessionState`: `idle`, `starting`, `collecting`, `researching`, `waiting`, `pausing`, `paused`, `blocked(String)`.
- `Models/BackgroundResearchModels.swift` defines native Codable `LearningStatus` matching Task 1's versioned fields/states, plus `BackgroundResearchRequest(registryURL: URL, campaignHash: String, runID: String, controlDirectory: URL, controlNonce: String)`. `EngineJob.backgroundResearch(BackgroundResearchRequest)` maps exactly to Task 3's CLI. A separate registration request invokes its registration command before first start; repeat registration verifies identity instead of resetting state.
- `PaperSessionPreferences`: Codable/Equatable with `learningEnabled=false`, `resumeOnLaunch=false`, `showMenuBarExtra=false`, `resourceProfile=.efficient`, selected registered paths/hashes; persist atomic preference changes, not trial evidence. Existing login preference remains separate.
- `BackgroundResearchService`: `start(campaignHash: String, registryURL: URL, configuration: EngineConfiguration) async throws`, `pause(reason: String) async`, `shutdown(timeout: Duration) async -> Bool`, observed `status: LearningStatus?` and owned-process state. Decode strict versioned status; reject source/campaign/run mismatches.
- `@MainActor @Observable PaperSessionCoordinator`: `start() async`, `pause() async`, `shutdown() async -> Bool`, `restoreIfOptedIn() async`, observed `state`; initialization injects collector, research service, preferences and resource monitor. `AppModel.paperSession` owns exactly one instance.
- `BackgroundResourceSnapshot` carries thermal state, low-power mode, memory/disk admissibility and collector health; `BackgroundResourcePolicy.shouldPause(_:) -> Bool`. Efficient workers: `min(max(cores / 2, 1), max(cores - 2, 1))`.

- [ ] **Write RED tests:** start twice launches one child; two consumers closing/reopening do not restart it; rapid Start/Pause epochs cannot publish late started state; user pause survives recovered resources; disabling learning leaves collection running; all resource reasons inhibit research dispatch; return to healthy only resumes an automatic pause. Verify worker counts for 1/2/4/8 cores are 1/1/2/4. Corrupt preferences default to no automatic work with an explanation. Changed source identity prevents opted-in resume.
- [ ] **Write shutdown/ownership tests:** checkpoint acknowledgement precedes stopped state; timeout is bounded at 30 seconds, records interruption, cleans up only owned children. Quit still drains any explicitly running legacy monitor without starting it. Collectors started outside the app are never acquired/killed. Use a fake clock and test child executables, not real study processes.
- [ ] **Run RED:** `swift test --package-path macos/Nowcaster --filter 'PaperSession|BackgroundResearch'`.
- [ ] **Implement** app-scoped services, routing existing collector Start/Stop through the coordinator without changing its qualification logic. Add awaited collector shutdown (reuse `stop()` and ownership). Extend engine command/runner ownership only as required for the new background job; preserve cancellation behavior for existing jobs. Do not cancel a research stream before asking it to checkpoint. At the deadline, verify ownership before TERM/KILL of its isolated group and retain interrupted state.
- [ ] **Move launch/resume orchestration out of `RootView.task`.** Bind delegate once at app scope; load view snapshots independently. Normal window close does not terminate the app. Delegate returns `.terminateLater` during coordinator shutdown, then replies once. `restoreIfOptedIn` is idempotent and cannot implicitly read broker credentials, activate the old monitor, or enable notifications. Explicitly disambiguate the existing legacy resume preference; do not silently transfer it to the new session.
- [ ] **Verify GREEN** with focused tests and `swift test --package-path macos/Nowcaster`; retain existing notification, runner, thermal, window and live-monitor regression tests.
- [ ] **Commit:** `feat: own paper sessions independently of Mac windows`.

### Task 5: Simplified native views and consistent menu/settings

**Files:** Modify `AppDestination.swift`, `RootView.swift`, `NowcasterApp.swift`, `Features/TradeDesk/TradeDeskView.swift`, `Features/StrategyLab/LivePaperSignalsView.swift`, `Features/StrategyLab/DayTraderContextView.swift`, `Features/LiveMonitor/LiveMonitorMenu.swift`, `Features/Settings/SettingsView.swift`; create `Features/TradeDesk/TradeDeskPresentation.swift`, `TradeAssetDetailView.swift`, `PaperHistoryView.swift`, `BackgroundResearchView.swift`, `PaperSessionSetupView.swift`; create `Tests/NowcasterAppTests/TradeDeskPresentationTests.swift`; update `UITests/NowcasterUITests.swift`, `TradeDeskUITests.swift`.

**Interfaces:**
- Add `AppDestination.history` and `.research`; retain all existing raw values. `AppDestination.primaryDestinations` is exactly `[.tradeDesk, .markets, .history, .research]`; others belong to Advanced.
- `TradeDeskPresentation.make(service: LivePaperSignalService, session: PaperSessionCoordinator, now: Date) -> TradeDeskPresentation` produces asset rows and one shared status/action. Row fields: symbol, source/freshness, trend, posture, reason and optional publication-bound detail. Both inspectors and menu recheck freshness at render/action time, not only when selected.
- Views use `model.paperSession` for Start/Pause, menu state and Research progress. Import/setup sheets invoke existing validated import actions; advanced research services remain available without becoming the default app lifecycle.

- [ ] **Write RED presentation tests:** no observations, stale/failed feed, expired open detail, stopped collection, unqualified candidate, synthetic history and valid experimental publication. Levels must be absent for every denied case and must disappear at expiry. Existing qualification/notification evidence cannot be replaced by a trend-only score. Unknown source types are labelled unavailable. History retains failed/losing records and marks unavailable costs instead of inventing zero.
- [ ] **Write RED UI tests** for four primary destinations, collapsed Advanced with all old routes still reachable, one Start/Pause session control, secondary setup/import actions, selected-asset details, History, resource state and Settings/menu consistency. Use stable identifiers, not the old chevron offset. Notifications remain opt-in; menu extra is optional with Dock/Open alternative.
- [ ] **Run RED:** `swift test --package-path macos/Nowcaster --filter TradeDeskPresentation`; build-for-testing and run the new focused XCTest cases, retaining failing evidence.
- [ ] **Implement content-first views.** Remove the oversized slogan and repeated instructions; keep a short Paper research label and relevant failure reason. Setup/instructions/methodology move to labelled secondary sheets/menus. Use native tables/lists, semantic colors and typography, resizable inspector, standard Settings/help/keyboard conventions. Separate connected BTC/ETH spot from imported/demo market lists. Successful imports are neutral success, not orange warning triangles.
- [ ] **Implement menu and preferences**: Open Nowcaster, same Start/Pause, collection/research status, Quit. Menu extra off until opted in. Provide separate controls for background learning, resume on launch, login startup, resources and notifications; explain closing versus quitting and sleep/offline limits. `openWindow(id: "main")`/Dock reopening restores without creating new work.
- [ ] **HIG audit during implementation:** consult Apple's current relevant pages for Mac design, layout, windows, sidebars, toolbars, menus, disclosures, lists/tables, inspectors, sheets, settings, typography, colors/materials, symbols, dark mode, accessibility, keyboards, VoiceOver, charts, writing/help/onboarding, progress, notifications, privacy and machine learning. Finish the catalogue applicability register; absent platform/capability guidance is explicitly not applicable, not falsely verified. No dependency on Figma or generated mockup assets is needed.
- [ ] **Verify GREEN** with full native suite and new XCTest cases. Inspect actual light/dark screenshots at 1440×900, 900×700 and the 820×620 minimum. Check focus order, keyboard access, labels, contrast, non-color status, larger-text accommodation and Reduce Motion/Transparency; label actual untested VoiceOver behavior as such.
- [ ] **Commit:** `feat: simplify the Mac trade desk and background controls`.

### Task 6: End-to-end background acceptance and single-branch delivery

**Files:** Create `macos/Nowcaster/UITests/BackgroundSessionUITests.swift`, extend `tests/integration/test_background_research_runtime.py`; update `README.md`, `.superpowers/audits/2026-09-28-interface-review/report.md` and add inspected after screenshots; maintain a separate progress ledger for this plan.

**Interfaces:** The installed signed app, existing public paper collector, Task 3 worker/status protocol, Task 4 coordinator and Task 5 views; no direct injection of fake profitability into the production UI.

- [ ] **Write RED acceptance tests** proving that after explicit Start and window close, the same owned collector produces fresh BTC and ETH receipts and the same research batch advances/checkpoints when eligible. Dock/menu reopen must not duplicate workers. Pause freezes new dispatch and stops collection; normal Quit exits all app-owned children; restart is stopped by default, separately tested opted-in resume verifies identity. Tampered checkpoint, lost lock and expired evidence remain visible failures.
- [ ] **Run focused RED**, then fix only failures attributable to this plan. Keep synthetic replay and research fixtures under marked `UIAcceptanceFixtures`, never in the real/frozen study. A synthetic training progression test proves control flow, not an edge.
- [ ] **Run changed release verification once:** full `python -m pytest -q`; Ruff format/check; `swift test --package-path macos/Nowcaster`; complete XCTest scheme with relevant opt-ins; existing helper/TLS/packaging tests and deterministic fixture/parity checks. Preserve old attempts and hashes; document any required source-only fixture identity updates. Do not rerun historical searches.
- [ ] **Run bounded real background acceptance:** explicitly start the existing public-data desk through the UI, close its window and observe for ten minutes. Require both assets' new provider/receipt timestamps, report count/latency/missing minutes and preserve all ledger prefixes. If training lacks eligible history, confirm honest Waiting state rather than manufacturing data. Reopen, Pause, Quit and verify child exit. No qualified alerts or credential access.
- [ ] **Perform final HIG/visual audit** on actual installed windows, including keyboard/VoiceOver where available. Keep an applicability/evidence table and accessible fresh/stale, progress, error, selection and narrow-window captures. Do not count untestable hardware/assistive states as passes.
- [ ] **Review and integrate:** follow the selected execution skill's task/final review gates. Resolve Critical/Important findings; explicitly retain Minor issues and declined-to-judge limitations. Update beginner-friendly README with Start/Pause, closed-window behavior, resource limits, sample prerequisites, strategy proposal versus promotion, sleep/offline gaps and Quit/resume choices.
- [ ] **Build and deploy:** sign the app/helper with existing local signing configuration; verify deep/strict signatures and bundle identity. After ordinary Quit, preserve the previous installed app in a uniquely named recoverable backup, install the new app and repeat installed-window/background lifecycle smoke tests. Never use a destructive replacement while jobs are active.
- [ ] **Commit/push** verified code/docs on `feature/research-round-2` only. Verify remote HEAD, working tree state and terminal CI result; record failures honestly. Final handoff distinguishes implementation/test completion from unqualified strategy performance and any genuinely unverified HIG behavior.

## Self-review and handoff

- Coverage: everyday interface → Task 5; background lifecycle/resources → Tasks 3–4; adaptive causal research/registry → Tasks 1–2; packaged integration/HIG/release → Tasks 3, 5–6.
- All five Review Focus items have named test requirements in their owning tasks. Native/Python status and campaign identities are explicit at the boundary.
- This plan adds scheduling and lifecycle around existing engines; it does not replace the engine, add a broker, relax gates or change the frozen study.
- Expected outcome can still be **Waiting** or **No qualifying strategy**. Those are retained research results, not reasons to re-run a consumed holdout.
- Written plan approved by the user on 28 September 2026; execution in progress.
