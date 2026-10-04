# End to end trader workflow implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Approval handoffs waived by the user's explicit autonomous-execution instructions.

**Goal:** A usable diagnostic paper trader connecting selection, context, setups, accounted position management and review.

**Architecture:** Extend the existing public-feed service with an opt-in separately versioned workflow journal. Pure selection and account transitions drive both recorded-feed tests and live processing; the native UI reads a validated summary, not executable orders.

**Tech Stack:** Existing Python/Pydantic/Decimal and native SwiftUI, no added dependencies.

**Spec:** `docs/superpowers/specs/2026-10-04-trader-workflow-design.md`

## Global Constraints

- Only supported credential-free Binance spot BTCUSDT and ETHUSDT; long/flat, no shorts, leverage, credentials, broker calls or qualified notifications.
- No writes to frozen study checkouts, environments or directories; no edits/reset of any old protocol, candidate, loss, gap, position or ledger.
- No installation, daemon, power-setting change or automatic replacement of the running app.
- Parameters fixed per manifest; learning cannot alter running rules, open positions or past decisions.
- Missing calendar/catalyst and quote evidence stays unavailable. Freshness and context gates remain intact.
- Work only on current `feature/research-round-2` worktree. Python environment is the main checkout `.venv`; no install. Parent handles final suite once across all tasks; workers run focused tests during TDD.

## Review Focus

- Stale or absent context cannot open a new trade; existing risk exits must survive it.
- Same-bar entry/exit and pre-entry extrema must never generate fictitious profit.
- Duplicate/revised data and process restarts must not duplicate trades or erase losses.
- Costs, sizing, lot rounding and daily limits must reconcile to retained account cash.
- Optional UI data must fail closed without poisoning legacy service state or old protocols.

### Task 1: Pure selection and paper account engine

**Files:** Create `src/research/trader_workflow.py`, `src/research/trader_workflow_account.py`, `tests/unit/test_trader_workflow.py`, `tests/unit/test_trader_workflow_account.py`.

**Interfaces:** Define immutable `WorkflowPolicy`, `WorkflowDecision` and `select_setups(round_protocol, observations, calendar, now, policy)` in trader_workflow. Consume existing ContextObservation/CalendarSnapshot/context extraction and cost screen. Define immutable `WorkflowAccount` and `advance_account(account, decisions, observations, now, policy)` in trader_workflow_account. Return account plus structured events/decisions via a named immutable transition result. Document actual signatures in report so runtime task uses them verbatim. Explicit policy/source identity and finite Decimal/time checks; bounded presentation history with lifetime counters, never discard accounting losses.

- [ ] RED: Test real aligned-context breakout/reclaim vs untriggered rising indicator, prior-window isolation, all blocked reasons/rank ordering, missing calendar/future/stale quotes and cost rejection.
- [ ] RED: Test later-only paper entry, bid/ask fees and sizes, stop-first ambiguous bar, pre-entry-extrema isolation, non-loosening trailing stop, stale exit persistence, max holding, one-position exposure cap, daily loss/trade caps and cooldown.
- [ ] Implement the exact workflow/values in the spec, using existing context and cost helpers. Pending intents retain immutable origin levels and identity. Completed outcomes feed descriptive setup-level aggregates; no performance-based rule changes.
- [ ] GREEN: Run new unit files and relevant existing context/cost tests; record RED/GREEN and self-review. Commit only owned files. No full-suite duplication; parent owns the final combined suite.

### Task 2: Durable opt-in collector integration

**Files:** Create `src/research/trader_workflow_runtime.py`, `tests/integration/test_trader_workflow_runtime.py`; modify `src/research/live_paper_signal_runtime.py`, `scripts/run_live_paper_signals.py`.

**Interfaces:** Consume Task 1 signatures from its report. Provide `enable_workflow(directory, now=None)`, `advance_workflow(directory, observations, calendar, now)`, `workflow_status(directory, now=None)`; all bind the loaded round and use `<directory>/diagnostic-workflow-v1`. Status is bounded JSON, paperOnly=true and schemaVersion=1; fields must include protocolHash, policyHash, updatedAt, state, reasons, decisions, account, positions, recentTrades, review. Persist manifest and hash-chained transition evidence; output exact native wire contract in report.

- [ ] RED: New temporary registered round, explicit activation, unchanged source protocol bytes, protected-directory refusal, enable idempotence and changed policy refusal.
- [ ] RED: Full recorded-feed path from decision through later entry and exit, restart/no duplicate input, changed duplicate/torn journal/regressing clock rejection, no retrospective fills from pre-enable history, separate error state and old-service compatibility.
- [ ] Implement enable/status commands and collector advancement only for explicitly enabled workflow. Advance after finalized observations are retained and before legacy strategy qualification gates; never reinterpret legacy suggestions as qualified. Do not catch corruption as a successful empty account.
- [ ] GREEN: Focused runtime/CLI/legacy live-paper tests; source manifests and event transitions checked on resume. Commit owned files, report exact wire contract and test evidence.

### Task 3: Native controls, complete verification and handoff

**Files:** Add native workflow model/view/tests under existing Models, Features/TradeDesk and NowcasterAppTests; modify `Services/LivePaperSignalService.swift` and TradeDesk/History views as needed. Update README and `docs/research/live-paper-signals.md`. Register new Swift files in Xcode if project uses explicit references.

**Interfaces:** Consume Task 2 status JSON/CLI. Add explicit Enable diagnostic simulator control for the selected registered source, disabled during transitions. Refresh via existing asynchronous owned-command service, no main-thread blocking. Show account and position data separately from qualified research suggestions; clear data on directory/protocol switches, failure and invalid payloads. Distinguish stale valuation from fresh money figures. UI describes hypothetical simulated fills and evidence limitations.

- [ ] RED: Native model reject wrong identity, non-finite/negative invalid data, malformed structure/future timestamps; state refresh clears stale or mismatched results. Model/presentation supports no-data, blocked, pending, open, closed and error.
- [ ] Implement compact accessible workflow cards, setup details and post-trade review without expanding the main desk into a wall of metrics. Source-supported markets/catalysts only. Document fixed policy, paper-only separation, how to enable and how background processing works.
- [ ] GREEN: Native focused tests/build; controller then runs full Python and native suites, fixture parity checks and fresh packaged build. Existing old engine may be retained outside verification to avoid falsely verifying stale binaries; preserve any moved artifacts.
- [ ] End-to-end recorded-feed evidence includes winners and losses after costs and complete account reconciliation; preserve journal, no synthetic sample presented as live performance. Final independent review, then commit/push on the same branch. No automatic install over active app/study.
