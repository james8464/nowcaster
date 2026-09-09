# Evidence-hardening verification — 9 September 2026

Status: local release verification passed; remote CI is recorded separately in the progress ledger. This is not a profitability finding.

## Scope and retained study

This revision corrects directional calibration, separates fit/selection/confirmation, adds threshold-search safeguards, rejects legacy mislabeled evidence for qualification, and explains open paper-position economics. The [live review](live-paper-review-2026-09-09.md) records the observations that motivated the reporting improvements. No historical strategy search or new research campaign was run.

The original study remains paper-only in `.worktrees/live-paper-study`, on its frozen source identity `1cb309297ac29de4f0d600de600595f86183586909d63bbdd4cc441224eb28c3`. Read-only verification at 06:16 UTC confirmed source/environment, retained discovery/registry, study ID and fixed end. Collector PID 74773 remained running in that checkout. No source, installed dependency, ledger, registry, rules, losses, gaps or study count was changed.

## Completed checks before final migration fix

- Baseline calibration/contextual checks: 38 passed.
- Calibration and evidence regression checks: 95 passed, with failing-before/fixed-after evidence recorded.
- Open-position diagnostics: 74 passed, including exact Decimal full/partial-exit economics, taint, stale marks and fixed-end immutability.
- Broader strategy, startup, snapshot and learning integrations: 41 passed.
- Both task-scoped independent reviews approved without findings.
- Deterministic CI fixtures regenerated; repeat generation had no drift. Schema v5 validation and Python/Swift semantic parity passed. These are bounded test fixtures, not a repeated historical strategy search.
- Ruff: 341 files formatted, all checks passed. Tracked/reachable-history secret scan passed.
- Native suite: 84 Swift Testing cases plus 1 XCTest passed. Release product compiled and the app assembled.

## Final review and migration fix

Final review identified one Important migration issue: old self-consistent receipts still asserting target-before-stop could survive the corrected producer. Commit `943fea8` adds an explicit versioned evidence-contract boundary without rewriting or deleting legacy records. Missing, unknown, malformed, incompatible and relabeled-return contracts fail closed. A separate synthetic event-contract control verifies structural compatibility only; there is no production event-calibration producer or verification of underlying event paths in this release.

The fix passed 163 focused tests in 88.66 seconds. A scoped independent re-review found the issue addressed and no new blocking regression. There were no unresolved Critical/Important review findings or policy rulings. The new contracts module imports without SciPy, as required by the deliberately lightweight live-engine bundle.

The initial full Python run was intentionally stopped for that fix after 80 passing tests (254.73 seconds); it is **not** a completed full-suite result. The fresh full-suite run on final source passed **1,132 tests in 754.19 seconds (12:34)** with no failures. Final fixtures have been refreshed, independently regenerated without drift, and validated with schema-v5 Python/Swift semantic parity `bc38efc04b37be36b37c95beb4682797390c32bda6a170d3a89fb5cb4d210257`; Ruff reports 344 formatted files and no lint findings. The tracked/reachable-history secret scan passed.

Full-suite command, from the isolated evidence-hardening checkout:

```text
PYTHONDONTWRITEBYTECODE=1 /Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.venv/bin/python -m pytest -q --tb=short -p no:cacheprovider
```

Source fix: `943fea8`; final deterministic fixtures: `4efb211`. No source files changed during the full run. Only documentation/integration records follow those revisions.

Final-fixture native verification passed: 84 Swift Testing cases in 22.665 seconds plus one XCTest, zero failures. The final app rebuilt successfully; deep/strict code-signature verification, Info.plist validation, and executable/source-manifest verification all passed. Packaging emitted optional-backend/platform-library warnings but completed successfully. The actual bundled executable completed the deterministic Binance replay with exit status 0: ready, healthy subscription, quote, finalized bar, then the fixture's deliberate provider-error event. No accounts, network market feed, or orders were used by this replay.

The pre-existing native foreground-window visual smoke limitation remains: the harness previously observed an 882×686 inactive window where 900×700 was required and could not activate it. Native tests and successful packaging do not establish that this visual smoke check passed. This revision changes no Swift view code.

## Integration and operational preservation

Fast-forward integration onto `main` preserved the exact tested source and fixtures. The integrated checkout then passed 118 targeted calibration, evidence, prospective-accounting and manifest tests in 3.91 seconds, plus the tracked/reachable-history secret scan. No full suite was repeated after the identical-tree merge.

At 06:48 UTC, read-only frozen-study verification again passed source/environment and retained manifest/discovery/registry checks. PID 74773 still used the original frozen working directory and command; its source checkout was clean. The 06:47:37 report was fresh and observing. Additional connection interruptions had already recovered; the latest gap record was 06:21:01.150907 UTC. The [dated live review](live-paper-review-2026-09-09.md) retains the updated coverage and economic snapshot without replacing the original observations.

The development worktree is retained with the built app and detailed RED/GREEN/review records. The existing quiet hourly study monitor tracks the new GitHub CI run separately; completed historical CI run `34265762701` remains successful and is not rerun. No frozen source/environment, retained study record, trading rule, account, order, alert or power setting was changed.

## Evidence interpretation

Correct software and passing tests do not establish a profitable trading strategy. Both frozen BTC/ETH candidates failed historical screening, and the observed live sample is tiny and gap-affected. No qualified alerts, broker orders or real-money readiness were enabled.
