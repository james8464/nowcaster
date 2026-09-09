# Historical replay clock-boundary correction

Binding specification: `docs/superpowers/specs/2026-09-09-historical-account-replay-clock-boundary-amendment.md`. This explicitly supersedes only the original replay's executable-input selection/count assertions. All trading/risk rules and immutable study boundaries remain unchanged. User authorizes autonomous choices and implementation; no further approval gate is needed.

### Task 1: Explicit preflight quarantine and amended evidence

**Files:** `scripts/run_historical_replay.py`, `src/research/historical_replay_reporting.py`, `tests/test_historical_replay_runner.py` only. Root owns specs, public docs, actual evaluation and final release. Core engine and shared archive parser must remain unchanged.

- [ ] Read the binding amendment and old Task2 report. Write failing synthetic tests first for the reproduced off-clock loader/engine mismatch and new strict preflight/evidence behavior; no real historical evaluation or downloads by implementer.
- [ ] Preserve exact raw pins/counts, partition clock-aligned rows after loader validation, and persist exclusive original-row quarantine/quality evidence before execution. Reject every other engine-contract violation in preflight; do not silently exclude unexpected anomalies.
- [ ] Bind amended v2 hypothesis/policy and original failed experiment identity/history in protocol, including actual prior parent reference when present; bind data-quality artifact hash through status/result and retain every failure.
- [ ] Extend reporting with raw/quarantined/executable/boundary-excluded counts and unchanged gap/account caveats. Use unchanged engine on filtered bars; no clock rounding or resampling. Add causal gap liquidation/cancellation and immutable candidate/cost tests.
- [ ] Close the previous nonblocking sibling-worker test gap with deterministic start synchronization, explicit survivor termination and retained partial evidence. Run focused runner/core tests, Ruff/format/diff checks, self-review and commit. Record RED/GREEN evidence; no broad review/subagents/full release suite.

## Controller checklist

- [ ] Preserve old source/round1 and record actual failure and timestamp audit before changing input selection.
- [ ] Review one bounded correction diff, resolve genuine defects with focused tests; freeze new source before a separately registered round2.
- [ ] Run exact-cache offline v2 once, retaining all outcomes. Verify source and live collector unchanged.
- [ ] Reconcile actual accounting/events/coverage independently; replace pending documentation with measured results, disclose amendment/failed attempt, update README.
- [ ] Complete final-source full Python/native/fixture/package checks, authorized main integration/upload and terminal CI monitoring. Retain all sources/research artifacts.
