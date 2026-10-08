# OANDA live paper indicator design

## Purpose and evidence claim

Nowcaster should perform the observable parts of an intraday trader's workflow: watch an exact broker market, identify a causal setup, decide long, short or no trade, publish a complete hypothetical order ticket, manage a paper position, and account for its net outcome. Positive return after costs is the research objective, not a property assigned to a signal or guaranteed by the app. The existing failed BTC/ETH study and all its evidence remain frozen and separate.

## Scope and identity

- This is a native macOS, **OANDA practice-only**, paper-trading indicator. No order endpoint, live-account endpoint, broker order, or qualified real-money alert may be reachable from this mode.
- Start with the four products verified on the user's practice account: `DE30_EUR`, `SPX500_USD`, `EUR_USD`, and `WTICO_USD`. Preserve OANDA's exact names and product terms. In particular, do not silently equate its `DE30_EUR`/“Germany 30” label with a Germany 40 CFD.
- Read the token from this Mac's Keychain only when starting the service; pass it to the child process without putting it in arguments, logs, status files, Git, or crash diagnostics. The token grants broad OANDA account access, but this code uses only practice market-data routes.
- Keep candidate configuration, strategy version, trading sessions, costs, risk settings, and broker product identity in an immutable manifest. A changed choice creates a new research round; it never rewrites prior results.

## Live-data and decision path

One supervised local collector subscribes to account-specific practice bid/ask quotes for the selected instruments and reconnects with bounded backoff. It records receive time, provider time, product, quote side, tradeability, disconnects and gaps in an append-only journal. No quote or bar is backfilled as though it had been seen live. Historical base-price candles may be used only in separately labelled exploratory research, never to warm a prospective account-quote decision invisibly.

Build five-minute bars from received quotes. A bar is usable only after it closes and a later quote seals it; gaps, stale or future timestamps, non-tradeable quotes, missing bid/ask, changed product identity, or insufficient coverage force no trade and a visible health reason. OANDA's sampled stream does not contain every tick, so its observed high/low cannot be presented as an exchange-wide or guaranteed executable extreme. Each strategy is evaluated at most once per sealed bar, and any entry uses a subsequent available quote on the correct bid/ask side.

Use the existing frozen opening-range, trend-pullback and range-reversion families as diagnostic hypotheses. Do not fit weights or parameters while the collector runs. An instrument may have at most one explicitly selected live paper rule in a declared round; until selection and its cost record are complete, that instrument is watch-only. Other families are evaluated in separate research rounds. Ineligible markets and sessions explicitly show no trade. A setup expires quickly and is removed when its quote, session, or evidence becomes stale.

## Paper ticket and lifecycle

Every paper entry records broker product and account currency, direction, strategy and reason, decision/quote/entry timestamps, units, notional, initial equity, effective leverage (`notional / equity`), broker margin rate and estimated margin, entry side/price, stop-loss trigger, take-profit trigger, time exit, estimated spread/slippage/commission/financing/conversion, and source/evidence identity. Show unavailable cost components as unavailable rather than zero. The ticket is hypothetical, not an executable order or personal recommendation.

Position sizing is capped by 0.25% initial equity risk per trade, 1% portfolio-wide daily realized-loss stop, 5% portfolio peak-to-trough drawdown halt, one position per product, and at most 1.0× aggregate effective notional leverage until a separately approved evidence round changes the cap. Convert notional and margin into the paper account currency using an available, timestamped rate; missing conversion blocks entry. Margin availability and contract unit precision must also be checked. Stops and targets are modeled on subsequent account quotes, with adverse-side fills, gap/slippage allowance, conservative same-observation ordering, and a compulsory session-end exit when a usable exit quote exists. A missing feed can leave a paper position unresolved; never mark it as closed at a convenient price. Preserve all opens, management events, closes, fees, losses and interruptions append-only across restarts.

Paper entries require a source-backed cost record for the exact product and an immutable session policy. If these are missing, the app may show observed trends as context but must say no paper trade; it must not manufacture profitability with assumed zero costs. A selected rule remains diagnostic until the predeclared historical and prospective gates pass. No automatic strategy promotion or retroactive retuning is allowed.

## Profitability and decision report

Rebuild a read-only report from the append-only paper ledger, never from editable UI state. Show total decisions, no-trade and rejected reasons, entries, closed wins/losses, unresolved/open positions, net and gross P&L, spread/slippage/commission/financing/conversion separately, net expectancy per closed trade, win rate, profit factor when defined, maximum drawdown, and eligible-session/feed coverage. Break results down by exact product, selected rule, direction and session; preserve a chronological trade list with the complete ticket and exit reason. No denominator may quietly omit losing, rejected, interrupted or censored outcomes. Do not count an unrealized mark or a touched target as realized profit. Show sample size and daily-block uncertainty, with “insufficient evidence” when the interval or sample is not meaningful.

Established opening-range, trend/pullback and range-reversion ideas are candidates to test, not strategies already proven profitable for these exact broker products. Rank or select rules only in a separately registered chronological research round that retains every attempted configuration, realistic and stressed costs, a sealed final period and the cash/no-trade comparison. The live rule stays frozen during its prospective paper period; “works most of the time” must be reported as an observed win-rate statistic with a sample and uncertainty, not a promised success rate. Profitability is judged primarily by net expectancy and drawdown after costs, not win rate alone.

## Native app and background behavior

The Trade Desk provides one explicit Start/Pause control for this separate practice mode, per-market live health and quote age, current no-trade reason or paper ticket, open paper positions, and completed net paper results. The primary view is compact; detailed provenance, costs, leverage, exits, evidence status, and the profitability/decision report are available on demand. A fresh experimental setup or material management/exit change may trigger an opt-in local notification labelled paper-only. Never notify on stale or incomplete data. Pausing stops new entries; if a paper position is open, the UI must prominently report whether management can continue and must never imply the stop exists at OANDA.

The supervised collector may continue with the window closed while the app remains running. Pause disarms new paper entries but keeps managing any open paper position; once flat, it stops. Quit stops it cleanly and retains any unresolved position as unresolved, never as a fabricated close. No OS daemon, power-setting change, or assumption of collection while the Mac is asleep/offline. On restart, a gap is retained and a new warm-up is required before any fresh signal. The existing BTC/ETH service and frozen study are untouched.

## Verification and release boundary

Tests cover token isolation, exact practice endpoints, instrument mismatch, quote parse/reconnect and stale-feed abstention, sealed-bar causality, duplicate and future-data rejection, next-quote paper entry, bid/ask stop/target and costs, leverage/risk caps, session-end behavior, journal replay and interruptions, report denominators/cost reconciliation and uncertainty, notification suppression, and native ticket/staleness presentation. Exercise the built and installed Mac app from the user's perspective, including window close/reopen and network interruption. Verify no order endpoint exists in the practice-mode dependency path.

Release the feature as an experimental paper indicator only. A profitable historical chart or short paper sample does not qualify it for real-money copying. The separate gate remains at least 90 calendar days and 100 closed prospective paper trades, reliable eligible-session coverage, positive net expectancy with a positive 95% daily-block-bootstrap lower bound, and compliance with predeclared loss limits. Report failures and uncertainty rather than hiding them.
