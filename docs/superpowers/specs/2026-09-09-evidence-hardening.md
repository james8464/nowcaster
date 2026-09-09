# Evidence hardening from live paper observations

Bounded changes to existing calibration and prospective-report flows. No new trading strategy, parameter search, or live-study intervention.

## Observations and intended outcome

At 2026-09-09 05:57:34 UTC study 99583f768b689fe2e83bdba42cef0f68c1068cc793262d8f30fb783f947cd6ff had one open BTC paper position, zero closed trades, no ETH position, 614/673 observed minutes per asset, and BTC liquidation-marked P&L -1.4606534428011 USDT. The open BTC position was gap-tainted, while the headline `tainted_trades` counter only counted closed trades. This is inadequate evidence to tune a strategy or identify a profitable asset.

A separate code audit reproduced losing short portfolio returns being sign-flipped into calibration wins. Bar-return labels were incorrectly described as target-before-stop events. Calibrator fit data were reused to score calibration and search thresholds. Fix these sources of false confidence and make retained open-position risks visible.

## Binding constraints

- Work only in `.worktrees/evidence-hardening`; the running `.worktrees/live-paper-study` source and installed environment remain unchanged.
- Never edit the retained study directory, ledger, registry, campaign counts, losses, gaps, rules or collector process.
- No dependencies installed/upgraded, account credentials, orders, qualified alerts, power-setting changes or OS daemons.
- Both existing candidates failed the 24-configuration historical screen and remain diagnostic only. No new historical search in this change.
- New tests use synthetic fixtures, not tuning to the ongoing study's P&L. Preserve all real observations and previous results.
- No probability or confidence value is a guarantee of profit. No claim of executable real-money profitability.

## Design

1. Calibration uses already-directional portfolio gross/net returns directly. Validate finite, nonnegative costs and gross minus cost equals net. Describe its outcome as `positive_strategy_return_after_costs`; preserve the live engine's target-before-stop requirement so incompatible evidence fails closed.
2. Use chronological 50% fit, 25% threshold-selection, 25% confirmation blocks. Purge training outcomes unavailable before selection begins and selection outcomes unavailable before confirmation begins. Never refit on confirmation. Score calibration on confirmation, select one threshold using selection only, then assess that fixed threshold on confirmation. Require adequate nominal and effective samples in every relevant stage; insufficient evidence abstains.
3. Threshold search uses unique thresholds, multiplicity-adjusted approximate Student-t bounds, and an effective-observation floor. Expose the method, threshold count and phase counts/boundaries in receipts. These are approximate screens, not distribution-free financial guarantees; repeated research campaigns still require independent governance.
4. Extend prospective reports with an explicit open-position gap-taint flag and diagnostic reason, plus frozen entry/SL/TP/expiry and model-based remaining-position P&L at stop/target/break-even. Clearly distinguish closed tainted-trade counts. Keep existing equity, P&L, execution, qualification and fixed-end accounting unchanged. No position means no invented plan. Stale quotes and partial exits must remain explicit.

## Acceptance

Regression tests catch losing-short calibration, falsely named outcomes, cost mismatch, threshold search optimism, dependent small effective samples, confirmation failure despite good development, time-boundary leakage, open taint, partial-exit economics, stale marks and fixed-end immutability. Relevant integration tests and one release verification follow; completed historical searches are not rerun.

## Sources and limits

- [scikit-learn calibration guidance](https://scikit-learn.org/stable/modules/calibration.html): fitting and evaluating calibration require appropriate separation.
- [Bailey et al., Statistical Overfitting and Backtest Performance](https://sdm.lbl.gov/oapapers/ssrn-id2507040-bailey.pdf): selection can manufacture apparent historical advantages.

These motivate evidence controls, not a claim that the revised algorithms make money. The observed sample is too small and interrupted for a performance conclusion.
