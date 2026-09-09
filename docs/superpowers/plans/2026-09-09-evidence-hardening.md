# Evidence Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove reproducible false-confidence errors and explain open paper-position risks without changing the running experiment.

**Architecture:** Extend the existing calibration functions and prospective summary/report pipeline. Reuse installed NumPy/SciPy/pandas; keep study state and financial execution unchanged. Future source identities differ and cannot replace the frozen collector.

**Tech Stack:** Python, NumPy, pandas, SciPy, pytest; existing native app consumer contracts.

**Spec:** `docs/superpowers/specs/2026-09-09-evidence-hardening.md`

## Global Constraints

- Work only in `.worktrees/evidence-hardening`; the running `.worktrees/live-paper-study` source and installed environment remain unchanged.
- Never edit the retained study directory, ledger, registry, campaign counts, losses, gaps, rules or collector process.
- No dependencies installed/upgraded, account credentials, orders, qualified alerts, power-setting changes or OS daemons.
- Both existing candidates failed the 24-configuration historical screen and remain diagnostic only. No new historical search in this change.
- New tests use synthetic fixtures, not tuning to the ongoing study's P&L. Preserve all real observations and previous results.
- No probability or confidence value is a guarantee of profit. No claim of executable real-money profitability.

Use `PYTHONDONTWRITEBYTECODE=1 /Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.venv/bin/python -m pytest ...` from this worktree. Do not install dependencies. Controller runs full release verification once, not each worker.

### Task 1: Honest calibration and independent confirmation

**Files:** Modify `src/models/calibration.py`, `src/strategies/validation.py`; tests `tests/unit/test_model_calibration.py`, `tests/unit/test_strategy_validation.py`, and narrowly relevant live-evidence tests. Document method in `docs/live-readiness.md`.

**Interfaces:** Keep `fit_strategy_oof_calibration(...)` return tuple compatible. Extend `SelectiveThreshold` with defaulted metadata if necessary. Add optional `minimum_effective_observations` to `selective_threshold`, defaulting to its nominal minimum. Preserve the target-before-stop requirement in `src/live_monitor/engine.py` and strict evidence loading; wrong definitions remain ineligible.

- [x] Write and run failing accounting tests. Equal directional strategy P&L must have equal profitability labels for long and short. A high-strength short losing .005 with gross -.004 and cost .001 cannot be a win. Validate input costs rather than taking their absolute value; malformed economics return unavailable. Existing fold-score expectation treating a losing short as a win must change.

```python
# Hand-checked economic fixture, not a market-price return:
net = np.array([-.005, .001] * 240)
gross = net + .001
# Use signal=-1 and strength=[.8,.1]*240; selected high-confidence
# losses cannot produce positive edge or target-before-stop evidence.
```

- [x] Implement direct `net_return > 0` labels and direct `gross_return` edge. Reject nonfinite economics, negative costs, and inconsistent gross-cost-net. Receipt definition becomes `positive_strategy_return_after_costs`; test the existing live gate rejects it. No gate weakening.
- [x] Add failing threshold tests: positive pointwise bound but negative adjusted bound across many thresholds; duplicate thresholds do not change results; serially clustered samples below the effective floor abstain; a strong balanced sample still selects. Run the tests before implementation.
- [x] Implement unique-threshold search with `alpha=(1-confidence)/number_of_unique_thresholds`, `scipy.stats.t.isf(alpha/2, df=effective-1)` and the existing effective-sample estimator. Require selected effective size at least the configured floor and greater than one. Count all attempted unique thresholds, including nonviable ones. Add metadata `candidate_count`, `bound_method`, and effective minimum; document approximation/dependence limits.
- [x] Write and run failing chronology tests: selection succeeds but confirmation loses; fit/selection outcomes crossing the next block's first decision are purged; mutating confirmation never changes fitted model/selected threshold; inadequate confirmation returns unavailable. Use at least 480 alternating synthetic rows for the positive regression, not the former 120-row self-fit example.
- [x] Implement boundaries `fit_end=n//2`, `selection_end=3*n//4`. Fit on prefix, choose threshold on middle, confirm it on suffix without refit; require strictly earlier outcome availability at both development boundaries. Require fit nominal/effective counts >= existing configured calibration thresholds, and confirmation report effective/nominal counts >= them. Selection and fixed-threshold confirmation require >=30 nominal/effective selected rows and existing 5% coverage. Report calibration metrics on confirmation, not fitting rows. If any stage fails, return unavailable with a precise reason and available phase counts.

```python
selection = selective_threshold(model.predict(selection_raw), selection_net)
confirmation = selective_threshold(
    model.predict(confirmation_raw), confirmation_net,
    candidates=[selection.threshold],
)
# Only confirmation-selected rows contribute returned edge/cost/uncertainty.
# edge - cost - uncertainty must equal confirmation.lower_net_edge.
```

- [x] Persist phase counts, purge counts, boundary timestamps, selection and confirmation bounds/counts, method and confidence scope in the receipt; include outcome availability in evidence hashing. Call the low-level fitter's report fit diagnostics rather than OOS validation; only the strategy-level chronological confirmation report earns the evaluation label.
- [x] Run focused calibration, strategy-validation and live evidence/gating tests plus Ruff for touched files; self-review, commit and record RED/GREEN results. No full historical runs or qualification changes.

### Task 2: Explain gap-tainted open positions and net economics

**Files:** Modify `src/research/prospective.py`, `src/research/prospective_runtime.py`; test `tests/unit/test_prospective.py`, `tests/test_prospective_runtime.py`; update `docs/live-paper-study.md`.

**Interfaces:** Additive summary fields: `open_position_tainted: bool`, `open_position_diagnostics: dict | None`; retain `tainted_trades` as closed-only. Markdown explicitly distinguishes them. Existing summary and qualification fields retain meanings; add a diagnostic open-taint reason without changing trading logic.

- [x] Write and run failing tests reproducing an open position crossed by a feed gap with zero closed trades. Expect open taint true, closed count zero, visible Markdown warning. With no position diagnostics must be null.
- [x] Add diagnostics using the frozen account selected by summary (fixed-end account after end), not the live post-end account. Include frozen entry, stop, target, expiry, remaining quantity, proceeds already realized, stale-valuation status. Do not mutate account/state or trigger fills.
- [x] Write and run failing tests for after-cost stop/target economics and partial exits. Use the exact current ledger model:

```python
exit_factor = Decimal('.9995') * Decimal('.999')
remaining_proceeds_at_level = quantity * level * exit_factor
trade_pnl_at_level = prior_proceeds + remaining_proceeds_at_level - entry_cost
break_even_bid = max(entry_cost - prior_proceeds, Decimal(0)) / (quantity * exit_factor)
```

Define these as modeled total trade P&L if the remaining quantity exits at that bid level, not guaranteed fills. Include additional stress on full entry notional separately. Existing `_equity` already marks to net liquidation value; never deduct exit costs twice. If stale, the frozen plan is still available but current valuation is stale. Do not invent executable liquidity, infer an exit event, or interpolate during gaps.
- [x] Implement diagnostics/report copy. Clarify stop triggers can gap and modeled profitability at target says nothing about probability of reaching it. Show after-cost reward/loss ratio only if target P&L positive and stop P&L negative; otherwise null. Break-even zero means prior proceeds already recovered cost, not a recommended price.
- [x] Test that reports after the fixed end retain end-window diagnostics even when later liquidation occurs. Run prospective focused suites and Ruff; self-review, commit and record RED/GREEN results.

## Release checks (controller)

- [x] Review each task and whole diff. Record actual live observation query/evidence without rewriting the retained study.
- [x] Run affected integration groups, then one full Python suite for the revised code. Native contracts are unchanged; run native tests/build only as needed for packaging, record existing foreground-window smoke limitation accurately.
- [ ] Confirm frozen collector remains healthy and source/environment unchanged. Do not interrupt it.
- [ ] Update beginner-facing README with actual improvements and remaining limitations. Merge and upload under the user's standing authorization only after clean verification; preserve all historical results and freeze records.
