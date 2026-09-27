# Native Trade Desk Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing paper day-trading workflow accessible and operational from the macOS app.

**Architecture:** Reuse the current retained causal evaluator and live collector. Add separately versioned starter strategy definitions and a non-destructive setup command; expose them through the native Trade Desk. Existing studies remain unchanged.

**Tech Stack:** Python/Pydantic, SwiftUI, Xcode application target.

**Spec:** `docs/superpowers/specs/2026-09-27-trade-desk.md`

## Global Constraints

- No credentials or orders.
- Preserve existing manifests and the frozen prospective study.
- Keep evaluation, calendar, data freshness, risk and notification gates unchanged.

## Review Focus

- Existing directories or symlinked protected paths must never be reset by setup (Task 1).
- Real configured strategies, not test-only stubs, must resolve and remain prefix-invariant (Task 1).
- Restart must reopen retained evidence without starting a collector or enabling notifications (Task 2).
- Stale snapshots must not masquerade as current trade ideas (Task 2).
- Xcode must launch an identified .app, with embedded runtime, not a bare package executable (Task 2).

### Task 1: Usable, retained paper research setup

**Files:** `src/research/round_two_runtime.py`, `src/research/paper_desk_setup.py`, `scripts/run_live_paper_signals.py`, `tests/integration/test_paper_desk_setup.py`.

**Interfaces:** `initialize_paper_desk(directory: Path, now: datetime | None = None) -> ResearchRoundProtocol`; CLI `setup --directory` returns normal status. Production registry adds unique `desk_*_1m` research variants, version `1.0.0`, fixed parameters copied from existing known generators. New default registration selects all three across BTC/ETH; legacy manifests are not migrated.

- [x] Add failing tests: real candidate resolution and causal rising/falling fixture; future suffix leaves prefix unchanged; repeated setup preserves bytes and ledger; arbitrary nonempty/protected symlink directory rejected.
- [x] Run focused tests and observe failure.
- [x] Implement idempotent setup and production starter definitions.
- [x] Run focused setup, research runtime and live runtime tests. Existing runtime filename is `test_research_round_two_cli.py`; combined checks passed.
- [x] Commit verified backend changes (`b2e26a2`).

### Task 2: Native Trade Desk and launch verification

**Files:** `AppDestination.swift`, `AppModel.swift`, `RootView.swift`, `Features/TradeDesk/TradeDeskView.swift`, `Services/LivePaperSignalService.swift`, `Features/StrategyLab/LivePaperSignalsView.swift`, native tests, README and audit report.

**Interfaces:** Service create/resume invokes Task 1 setup then existing open; calendar import invokes existing validated CLI. Native desk composes existing controls/context without depending on a snapshot. Default Trade Desk; preserve non-Today saved destinations. Controls remain paper-only and opt-in.

- [x] Add configuration validation tests for new setup without manifest and protected paths; run failing.
- [x] Implement setup/calendar controls, Trade Desk with supported assets/playbook and honest evidence prerequisites. Scope research toolbar to research destinations.
- [x] Run `swift test`: 126 Swift Testing + 4 XCTest passed. Expanded Python checks: 173 passed; full-suite checkpoint: 1,624 passed, two packaged-helper tests skipped pending separate execution.
- [x] Build real Xcode project, confirm bundle identity/helper, and verify bounded packaged public-feed start/stop. Resolve observed Shortcuts bundle rejection using existing Apple Development identity and ignored local Xcode signing settings.
- [ ] Complete create/resume/start/stop/context and lifecycle interactions through UI. Blocked by repeated native-control service crashes; actual app launch and system-service acceptance verified, not a substitute for this check. See retained audit.
- [ ] Update README/Xcode guidance and audit, commit, obtain one fresh review, apply important fixes, and push the same branch under standing authorization.
