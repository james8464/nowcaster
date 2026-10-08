# SPX500 Last-Half-Hour Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Test one pre-registered, causal SPX500 CFD hypothesis without touching the active prospective study.

**Architecture:** A small pure evaluator consumes OANDA historical M5 bid/ask candles and returns one exploratory daily outcome or a specific abstention. A separate read-only research runner can fetch bounded historical windows, preserve source and attempt hashes, and publish development/validation/sealed comparisons.

**Tech Stack:** Python, pytest, OANDA practice read-only API, `zoneinfo`, `Decimal`.

**Spec:** `docs/superpowers/specs/2026-10-08-spx-last-half-hour-research.md`

## Global Constraints

- Paper-only; no order endpoint, no credentials in outputs, no edits to active/frozen studies.
- One product and one fixed rule; baseline/stress costs and all attempts retained.
- Historical base-price outcomes are exploratory and not executable account fills.

## Review Focus

- US DST transition: expected NY clock slots still map to 78 unique UTC bars.
- Missing prior full session: abstain without fabricating first-half-hour change.
- Missing current bar: abstain, not forward-fill.
- Same-bar stop and target: adverse stop first.
- 15:30 confirmed-bar decision: entry must be no earlier than the 15:35 open.

---

### Task 1: Pure causal evaluator

**Files:** Create `src/intraday/last_half_hour.py`; create `tests/unit/test_last_half_hour.py`.

**Interfaces:** `evaluate_last_half_hour(prior: Sequence[ConfirmedBar], current: Sequence[ConfirmedBar], *, slippage_points: Decimal) -> DayOutcome` returns an explicit abstention or one USD-point outcome with direction, decision/entry/exit timestamps, bid/ask entry/exit, stop/target and reason.

- [ ] Write synthetic two-day tests for long, short, disagreement, missing bar, DST and stop-first.
- [ ] Run the focused tests and verify expected missing-feature failures.
- [ ] Implement exact spec behavior with no lookahead or historical account-fill claims.
- [ ] Run focused and broader intraday tests; commit.

### Task 2: Immutable research runner and report

**Files:** Create `scripts/run_last_half_hour_research.py`; create `tests/integration/test_last_half_hour_research.py`; update `docs/research/research-round-2.md` only with a link to distinct round artifacts.

**Interfaces:** runner reads historical JSONL captures, checks exact instrument/source/coverage, hashes inputs, refuses duplicate or altered rounds, records all attempts, stage summaries and baselines. It never labels a historical result prospective.

- [ ] Write failing tests for immutable manifests, cost stress, missing data and sealed-once rule.
- [ ] Run focused tests and verify failure.
- [ ] Implement only the fixed registered rule, three chronology windows and report.
- [ ] Fetch eligible historical data into a new directory, run development then validation, freeze before sealed inspection, and report honestly. Run focused and broader tests; commit/push to the existing feature branch.
