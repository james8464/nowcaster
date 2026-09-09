# Live readiness

“Live Locked” is the expected state. A positive backtest, high confidence score, or profitable week is not enough.

This page concerns real-money order execution. The Live Monitor does not unlock that path: its long, short, entry, stop, target, and close events are hypothetical notifications only. Passing its alert gates means the current setup is consistent with one sealed research cohort; it does not mean capital is safe or that the strategy is ready for live orders.

Every gate must pass for one unchanged cohort:

- at least 60 completed equity sessions or 90 crypto calendar days;
- at least 100 closed paper trades;
- zero unresolved reconciliation differences, unknown broker events, causal failures, or health breakers;
- positive observed paper edge and positive edge under stressed live costs;
- matching causal/no-repaint evidence;
- bootstrap probability and deflated-Sharpe probability at least 95%, probability of backtest overfitting at most 40%, parameter stability at least 70%, and bounded slippage-model error;
- a readiness receipt issued in the last 24 hours;
- the exact bundled engine, a production Developer ID signature, hardened runtime, notarization, stapling, and external security review;
- matching live account and live-only Keychain credentials; and
- an explicit manual arm that expires within 30 minutes.

Changing any cohort component resets the clock and trade count. A breaker or mismatch invalidates readiness immediately.

Offline search can only create a **shadow cohort**. The challenger begins with zero forward evidence and an explicit rollback target; it cannot inherit the incumbent's profitable days. Forward qualification requires the unchanged shadow cohort to pass every gate. A materially wrong execution-cost model, calibration deterioration, or confirmed model drift invalidates its short-lived receipt and blocks new alerts.

Paper fills are compared with the model's predicted fee, spread, slippage, impact, and latency. The execution-cost model must have enough observations and its upper confidence bound on relative error must pass policy. This does not make paper fills identical to real fills; it makes unmeasured execution error a reason to stop.

The first pilot ceilings cannot be raised by configuration: each position is the smaller of $100 or 0.10% of equity; gross exposure is the smaller of $500 or 0.50%; daily loss is the smaller of $25 or 0.05%. Entries are price-collared marketable limits, equity positions cannot enter extended hours or remain overnight, and shorts require broker-confirmed shortable/easy-to-borrow status.

These gates reduce known risks; they do not make returns predictable or capital safe.

Offline strategy calibration measures `positive_strategy_return_after_costs`: whether the already-directional portfolio net return is positive. Long and short portfolio returns use the same accounting; gross return minus finite, nonnegative cost must equal net return. This is not a target-before-stop event. The Live Monitor still requires `target_before_stop_after_costs`, so these bar-return receipts cannot qualify an alert.

Calibration now splits the ordered out-of-fold observations into 50% fit, 25% threshold selection, and 25% independent confirmation. A fit outcome must be available strictly before selection starts, and a selection outcome strictly before confirmation starts; crossing outcomes are purged. The calibrator is never refitted on confirmation. Its low-level report describes fitting diagnostics, while the strategy receipt's Brier score, log loss, calibration error, outcome counts and intervals use confirmation only. Fit and confirmation must satisfy the configured nominal and effective calibration sample floors. Threshold selection and fixed-threshold confirmation each require at least 30 nominal and effective selected observations and 5% coverage. A failed phase makes the evidence unavailable.

Threshold search counts every unique attempted threshold, including thresholds with inadequate coverage. For confidence `c` and `m` candidates, each bound uses `alpha=(1-c)/m` and the two-sided Student-t critical value with `effective_observations-1` degrees of freedom. Confirmation assesses only the one previously selected threshold. Receipts retain phase and purge counts, decision boundaries, fitting diagnostics, selection and confirmation bounds, candidate counts, confidence scope, and an evidence hash that includes outcome availability. Returned edge, cost, and uncertainty use confirmation-selected rows, with edge minus cost minus uncertainty equal to the confirmation lower net-edge bound.

These bounds are approximate screens. Effective size uses a finite-lag autocorrelation heuristic that can miss longer or nonlinear dependence; Student-t and multiplicity adjustments do not provide distribution-free coverage for financial returns. The confidence scope covers the stated threshold family and its separate confirmation, not repeated research campaigns. Independent campaign governance and all existing live gates still apply.
