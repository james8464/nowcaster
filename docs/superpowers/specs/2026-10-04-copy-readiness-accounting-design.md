# Copy-readiness accounting and receipt design

## Intent and limits

Nowcaster should help its owner evaluate hypothetical day trades and, eventually, decide whether a tightly capped real-money pilot is warranted. It must not turn a paper gain, a backtest, or an incomplete receipt into permission to trade. The frozen September prospective study stays unchanged and diagnostic; both BTC and ETH failed its historical screen and the retained gap remains evidence.

## Chosen scope

Harden the existing forward-readiness gate, rather than add more signals or a broker connection. For each period, validate that paper and stressed returns are finite and greater than -100%, drawdown is between 0 and 100%, and stressed return does not exceed paper return. Combine daily returns geometrically (`product(1 + return) - 1`), because arithmetic sums can be positive when an account has lost money. Both paper and stressed compounded results must be positive.

An issued base readiness receipt is valid only if it carries the exact current set of unique, passing gates. Add a new `return_accounting` gate and require it in the live-monitor receipt contract too. This invalidates older receipts lacking the stronger accounting proof. A receipt remains time-limited and cohort-bound. No rule changes or retroactive edits are made to any existing study.

The read-only monitor must not import trading or broker modules. Put the shared gate-name contract in a neutral source module so the monitor/trading boundary remains enforced.

Statistical robustness fields are also required to be finite and within their feasible ranges: probabilities, PBO, and parameter stability in [0, 1], and slippage-model error nonnegative. Otherwise an infinity or negative error could falsely satisfy a one-sided threshold.

## Alternatives rejected

- Loosen thresholds to obtain more signals: risks selecting noise and does not fix execution uncertainty.
- Enable a small live broker pilot now: current strategies failed screening and have insufficient forward trades; a small cap limits loss size but cannot create an edge.

## Verification and residual risk

Red/green tests must show arithmetic-positive but compounded-negative returns lock readiness; impossible stressed returns and drawdown lock it; an incomplete or duplicate gate list cannot unlock the broker-facing receipt. Existing valid evaluator receipts remain valid. Run affected readiness, arming, live-monitor persistence, and native tests. Refresh deterministic research fixtures when source identity changes. Even a passing receipt cannot prove future profits or real fill quality; the app remains paper-only for the current cohort.
