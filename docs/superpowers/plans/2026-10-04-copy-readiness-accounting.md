# Copy-Readiness Accounting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent false real-money readiness from misleading return arithmetic or incomplete receipts.

**Architecture:** Extend the existing pure `ReadinessEvaluator` with a return-accounting gate and geometric aggregation. Make receipt validation require the same exact gate set in both the trading and live-monitor paths; keep current prospective collection isolated.

**Tech Stack:** Python 3.13, Pydantic, pytest, deterministic research fixtures, SwiftUI native app.

**Spec:** `docs/superpowers/specs/2026-10-04-copy-readiness-accounting-design.md`

## Global Constraints

- No account credentials, orders, or changes to the frozen prospective study.
- No profitable or safe-to-copy claim from backtests or paper results.
- Same GitHub branch: `feature/research-round-2`.

## Review Focus

- A +60% day followed by a -50% day must fail a positive-edge gate despite a positive arithmetic sum.
- Stressed return above paper return must fail closed.
- Return at or below -100%, missing return, non-finite return, or drawdown outside [0, 1] must fail closed.
- A legacy or duplicate-gate receipt must not pass `valid_at` or reach the broker adapter.
- A fully formed, current evaluator receipt must still pass its pure validation path.

---

### Task 1: Geometric forward-accounting gate

**Files:** `src/trading/readiness.py`, `tests/unit/test_live_readiness.py`

**Interfaces:** `ReadinessEvaluator.evaluate(...) -> ReadinessEvaluation` retains its signature; adds `return_accounting` and computes existing paper/stressed edge gates from compounded returns.

- [x] Add tests for arithmetic-positive/compounded-negative, impossible stressed return, negative or excessive drawdown, and return at or below -100%.
- [x] Run the new tests and confirm failures for the intended missing behavior.
- [x] Implement minimal return validation and geometric aggregation, then run the readiness suite.
- [x] Update `docs/live-readiness.md` to explain the new gate.
- [x] Regression-test and reject non-finite or out-of-range robustness fields that would pass a one-sided threshold.

### Task 2: Receipt contract and native lock

**Files:** `src/trading/readiness.py`, `src/live_monitor/evidence.py`, `tests/unit/test_live_broker_lock.py`, `tests/unit/test_live_arming.py`, `tests/unit/test_live_monitor_evidence.py`

**Interfaces:** `ReadinessReceipt.valid_at(instant, cohort_hash=...) -> bool` requires the exact unique gate set; `ActiveReadinessReceipt.valid_at` requires the same new gate in its own contract.

- [x] Add failing tests for incomplete/duplicate gate receipts in the base, broker, and live-monitor paths; adapt positive fixtures to use exact gates.
- [x] Run the tests and confirm they fail for the intended contract gap.
- [x] Implement exact gate-set validation; run affected suites.

### Task 3: Deterministic evidence and delivery

**Files:** `data/research/ci/*`, `macos/Nowcaster/Sources/NowcasterApp/Resources/Fixtures/nowcaster-snapshot.json`

**Interfaces:** Existing fixture generation and Python/Swift parity scripts; no runtime interface change.

- [x] Regenerate deterministic research output and merge only research sections into the existing native fixture.
- [x] Verify Ruff, affected Python tests, native Swift tests, and fixture parity; run full Python suite or report any local stale-build limitation by name.
- [ ] Commit and push the same branch; inspect GitHub CI and report its terminal status or clearly state if still running.
