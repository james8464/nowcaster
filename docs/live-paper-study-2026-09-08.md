# Live paper experiment — 8 September 2026

## What this establishes

This is a prospective measurement of hypothetical trades using public live BTC/USDT and ETH/USDT spot prices. It does not place orders, hold money, borrow, short, or unlock the app's qualified trading alerts. Live prices are real; balances and fills are simulated.

The [historical search](holding-period-search-2026-09-08.md) retained all 24 tested variations. Every variation lost on average after modeled costs. The two selected rules are diagnostic only and permanently fail the historical-screen requirement. This study cannot turn a lucky profitable streak into approval to trade them.

## Registered experiment

- Study: `99583f768b689fe2e83bdba42cef0f68c1068cc793262d8f30fb783f947cd6ff`, campaign 1.
- Fixed start: **8 September 2026, 18:44:12.805040 UTC**.
- Fixed end: **7 December 2026, 18:44:12.805040 UTC**.
- Frozen source/runtime identity: `1cb309297ac29de4f0d600de600595f86183586909d63bbdd4cc441224eb28c3`.
- Independent starting balances: 10,000 paper USDT each; these are not a combined portfolio.
- BTC rule: volatility-scaled trend, two-ATR stop, three-ATR target, twelve-hour limit.
- ETH rule: Bollinger/Keltner squeeze, one-ATR stop, 1.5-ATR target, twelve-hour limit.

The manifest, complete discovery, append-only campaign registry and SQLite ledger live outside Git under `~/Library/Application Support/Nowcaster/ProspectiveStudies/study-001-20260908`. The dedicated source checkout is `.worktrees/live-paper-study`. Keep it and its installed environment unchanged; use another checkout for further development. Changes to documentation do not alter its source identity.

A detached collector was launched from that checkout as process 74773. Process IDs can be reused, so later checks must also verify its command, working directory, report age and last feed event. The existing hourly Codex heartbeat checks this same study, leaves a healthy collector alone and resumes stopped collection without resetting evidence. This is not an operating-system service: the Mac must remain awake and online. No power settings were changed.

## Live observations

At **18:50:30.341961 UTC**, the collector was observing normally, with no reported error. It had accepted **747 quotes**, recorded six completed observation minutes per asset with 100% coverage over that very short span, and recorded **zero decisions, fills or closed trades**. Both independent paper balances remained 10,000 USDT, with zero P&L and no open exposure. The rejection counter was 196 during startup and stayed unchanged in the subsequent observed sample; those observations were not used as trades or coverage. The single recorded gap was collector startup. This is a short operational check, not a full trading day or evidence of profitability.

A read-only, consistent SQLite snapshot at 18:44:42.019621 UTC independently passed the manifest identity, journal hash chain, state digest, gap-index and valuation-index checks while the writer continued running. The database structural check also passed. Small exchange clock leads no longer prevent valid post-start quotes from being accepted.

Earlier, a separate transport diagnostic from 18:06:27.858561 to 18:08:34.695251 UTC received 247 quotes and four closed minute candles. It exposed an exchange clock lead of approximately 90–172 milliseconds. The corrected runtime waits out small clock differences while retaining the original receipt timestamp, and still requires both provider and receipt timestamps to clear the entry delay. That transport diagnostic predates registration and is not study performance evidence.

The collector cannot make a decision from its seed history or initial partial hour. Only an entire, promptly observed live hour can generate a prospective decision. A decision is not a fill, and an open position is not a successful closed trade. The continuously updated local `report.md` and `summary.json` are authoritative for later observations; this dated document is a snapshot, not a live dashboard.

## Engineering checks and limitations

- The actual historical search was completed twice; every trial, selection and archive manifest reproduced unchanged.
- Independent code review and a scoped outage-fix re-review found no remaining important blockers. The re-review checked 3,200 varied outage intervals against a minute-by-minute reference and eight restarts.
- The final outage benchmark retained 86,402 gap records for a simulated 24-hour, two-symbol outage. Working state stayed at 1,478 bytes; resumed quote processing measured 0.142 milliseconds per quote on this Mac. This is a software benchmark, not a trading simulation result.
- Focused collector/ledger tests: 70 passed. Native tests: 84 Swift Testing tests and one XCTest passed. The native app rebuilt successfully and passed signing and bundled-engine identity checks. Research fixture reproducibility, Python/Swift data parity, formatting, lint and tracked/history secret checks passed.
- The complete Python suite passed: **1,087 tests**, 10 warnings, 88% code coverage, in 17 minutes 48 seconds. Code coverage measures exercised program lines, not prediction accuracy. An earlier incomplete run was deliberately stopped before the final outage fix; it is not counted as a completed test run.
- The separate native window smoke check failed twice: expected 900×700, observed 882×686. The test app remained inactive despite an activation request, and the dimensions were exactly 98% of the requested size. The screen was large enough. This suggests a macOS focus/presentation limitation, but running-app content geometry was not independently confirmed. This pre-existing harness failure is **not** a passed visual test or an observed app crash; no UI source changed in this round and no screen/accessibility permissions were altered.

Paper execution assumes displayed liquidity, modeled slippage and a fixed fee rate. It cannot reproduce actual queue priority, all market impact, broker rejection or a particular account's execution. Even a successful prospective paper result would require additional evidence before relying on real-money execution. See the [study guide](live-paper-study.md) for the exact costs, risk limits, outage treatment and fixed-end assessment.
