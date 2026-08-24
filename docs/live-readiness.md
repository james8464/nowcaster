# Live readiness

“Live Locked” is the expected state. A positive backtest, high confidence score, or profitable week is not enough.

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

The first pilot ceilings cannot be raised by configuration: each position is the smaller of $100 or 0.10% of equity; gross exposure is the smaller of $500 or 0.50%; daily loss is the smaller of $25 or 0.05%. Entries are price-collared marketable limits, equity positions cannot enter extended hours or remain overnight, and shorts require broker-confirmed shortable/easy-to-borrow status.

These gates reduce known risks; they do not make returns predictable or capital safe.
