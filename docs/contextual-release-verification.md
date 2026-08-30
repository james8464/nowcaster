# Contextual allocation release verification

Verification started 30 August 2026 and continued on 31 August. This record covers the contextual asset-selection and strategy-weighting implementation, not proof that any strategy will make money.

## What is implemented

The native Mac app can assess its configured markets, explain eligibility and market conditions, show asset-specific strategy influence, and run bounded contextual learning. The engine uses partially pooled estimates, covariance-aware weights, portfolio constraints and an explicit cash option. Research challengers remain in shadow state and cannot replace a qualified live model automatically.

Final review tightened historical cohort authentication, timestamp integrity, shared-capital accounting, execution-horizon checks, live evidence expiry and persistent drift quarantine. Binance liquidity checks now require a fresh verified full order book, matching quotes and exchange size rules. Missing evidence blocks eligibility.

## Verification evidence

| Check | Result |
|---|---|
| Complete Python regression suite | 948 tests passed in 12 minutes 28 seconds |
| Focused contextual suite | 81 tests passed |
| Python formatting, static checks and whitespace | Passed; 316 Python files checked |
| Native tests | 84 Swift Testing cases and 1 XCTest passed |
| Native release build | Passed |
| Deterministic research generation | Passed; post-commit regeneration produced no fixture differences |
| Python/native research fixture parity | Passed |
| Live-monitor safety target | 110 Python tests, 12 native tests and recorded replay passed |
| Packaged notification engine | Live Bitcoin/Ether quotes, clean shutdown and recorded replay passed |
| Local app assembly, property list and signatures | Passed; ad-hoc signature only |
| Tracked-file and reachable-history secret scan | Passed; repeated before publication |
| Independent final reviews | Four boundary, 14 research-identity, 41 provider/TLS and 20 shutdown/migration regressions passed; no remaining Important/Critical findings in those review scopes |

The contextual command-line acceptance run used the available `csv/ci-fixture` Bitcoin 5-minute development evidence. Its portfolio replay evaluated 20 decisions after 40 warm-up timestamps and returned **all cash**, with zero return. Historical executable liquidity was unavailable. This is the expected refusal to invent fills, not a profitable-market result. The four-decision retrospective holdout is explicitly not an independent sealed test.

Regression coverage includes future-tail invariance, exact decision-time execution matching, short-return sign handling, fees and turnover, duplicate-asset sample accounting, unsupported holding periods, mismatched or tampered evidence, expiry and same-time/later drift that survives repeated refreshes.

The initial complete run passed 918 tests and exposed one reproducibility regression: an output database path entered the contextual evidence hash. The fix separates scientific configuration from storage location, uses source content instead of Git receipts, and binds actual code/policy changes into the cache and protocol-v2 outcome identity. Targeted tests reproduce the original failure and verify the correction; the final full-suite result is recorded above.

## Public-market connection checks

A bounded, read-only check against Binance loaded Bitcoin and Ether instrument metadata, validated a 100-level Bitcoin order book on both sides, and decoded live trades, depth updates and quotes through the production adapter. No trading credentials or order endpoint were used. This verifies transport and parsing, not signal quality or profitability.

The check exposed three compatibility issues that deterministic fixtures had missed. The provider now understands Binance's current `permissionSets` structure without overlooking additional account requirements, uses compact JSON for multi-symbol requests, and supplies the packaged certificate bundle to verified WebSocket connections. Certificate and hostname verification remain enabled; missing certificates fail closed. Regression tests cover the corrected provider and TLS contracts. These changes follow the [Binance API documentation](https://developers.binance.com/docs/binance-spot-api-docs) and [Python's verified SSL context guidance](https://docs.python.org/3/library/ssl.html#ssl.create_default_context).

The full packaged-engine check then caught a persistence defect: real exchange update IDs already exceed 32-bit integer storage. Schema 14 widens the shared sequence field to 64 bits and migrates existing records without changing their immutable payloads. New tests reproduce the observed `99417023643` update ID and verify migration, preservation and idempotency.

Failure injection also exposed blocked stdin shutdown and unobserved control-task failures. Private controls now use bounded cancellable pipe reads; bootstrap reading cannot swallow an early shutdown command. Internal failures emit a sanitized terminal event and exit nonzero even with stdin still open. Shutdown discards an already-queued market event and closes the producer before disposing the database. Native supervision allows a bounded three-minute cold start while retaining its 45-second health timeout after readiness. The complete suite is rerun after these changes; interrupted earlier runs are not counted as passes.

The final packaged app helper received fresh `BTCUSDT` and `ETHUSDT` quotes on 31 August 2026 local time, with approximately 135 ms from provider timestamp to receipt in this short observation. It reported zero qualified cohorts, issued no entry notification, accepted shutdown and exited successfully without diagnostics. This is a bounded connectivity smoke test, not a latency guarantee or a forward-trading trial. A low-battery hibernation interrupted an earlier test run; the affected startup test and the entire live-monitor target passed after wake-up.

## Native toolchain compatibility

The [previous GitHub native build](https://github.com/james8464/nowcaster/actions/runs/33208597850) exposed an Xcode 16.2 concurrency error in notification settings. Notification authorization now projects a Boolean inside Apple's callback instead of passing the older SDK's non-sendable settings object across actors. The allowed authorization states are unchanged, and no concurrency warning is suppressed. The added authorization regression and the complete native suite pass locally; the repository's CI remains the check against its pinned older toolchain.

## Remaining external limitations

- **Manual visual inspection:** the rebuilt app launched and exposed its native window to the accessibility interface, but the computer-use skill's connection then failed repeatedly, including after resets and a retry after the Mac woke. The new screens have automated native coverage, not a completed manual screenshot/HIG audit for this build.
- **Public distribution signing:** no Developer ID identity was available. This local build is ad-hoc signed, not notarized; the production-signing release gate was not claimed or bypassed.
- **Market validation:** deterministic fixtures and historical development outcomes are not live execution evidence. No order was placed, no strategy was promoted for real money, and no profitability claim is made. Historical coverage is limited to available, licensed, verified provider data; missing order books, borrow and unavailable history cannot be reconstructed from candles.

See the [beginner guide](../README.md), [methodology](strategy-methodology.md), [backtest contract](backtest_protocol.md) and [Live Monitor guide](live-monitor.md) for operation and safety boundaries. The [earlier native verification](native_verification.md) is a historical record, not the results of this build.
