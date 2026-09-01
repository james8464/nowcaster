# Day-trading opportunity audit — 1 September 2026

## Outcome

Nowcaster did not find a reliable day-trading strategy in this audit. All six asset/timeframe scopes returned `no_reliable_strategy_found`; zero of 140 direction-specific hypotheses passed the predeclared development and validation gates, and real-money status remains locked.

This is the safe result. The program identified many historical indicator setups, but an indicator firing is not automatically a usable trade. The unqualified diagnostic consensus lost between 32.96 and 36.27 basis points per holdout setup after modeled costs. Mean return before costs ranged from -2.27 to +1.04 basis points, so the tested rules were not close to covering the conservative 34-basis-point round trip.

## Evidence

- Window: 17 August 2017 through the exclusive 1 September 2026 UTC cutoff.
- Assets: Binance Spot `BTCUSDT` and `ETHUSDT`.
- Timeframes: 5 minutes, 15 minutes, and 1 hour.
- Source: 918 official Binance monthly/daily ZIP files, each verified against its published SHA-256 checksum.
- Archive manifest SHA-256: `315cb5139ba2ca3d52a3ad7a29673f7d456870a330418a8f0c47794170e65a74`.
- Rows tested: 2,689,416 finalized candles across the six scopes.
- Data defects: 96 impossible archive boundary rows were excluded; every resulting gap reset indicator warm-up and invalidated any setup that crossed it.
- Chronology: fixed 60% development, 20% validation, and 20% untouched holdout.

| Asset | Timeframe | Candles | Missing candles | Directional hypotheses | Rules selected | Diagnostic holdout setups | Mean after costs |
|---|---:|---:|---:|---:|---:|---:|---:|
| BTCUSDT | 5m | 949,208 | 1,720 | 22 | 0 | 9,217 | -33.76 bps |
| BTCUSDT | 15m | 316,398 | 578 | 30 | 0 | 10,540 | -33.28 bps |
| BTCUSDT | 1h | 79,102 | 142 | 20 | 0 | 3,422 | -33.95 bps |
| ETHUSDT | 5m | 949,208 | 1,720 | 22 | 0 | 8,467 | -33.83 bps |
| ETHUSDT | 15m | 316,398 | 578 | 28 | 0 | 10,680 | -32.96 bps |
| ETHUSDT | 1h | 79,102 | 142 | 18 | 0 | 3,256 | -36.27 bps |

The 45,582 diagnostic holdout setups above combine every tested rule only to measure the raw library. They are not a selected strategy and must not be traded.

## What “properly tested” means here

- A decision uses only a fully closed candle and enters no earlier than the next continuous candle.
- One hypothetical position per rule is allowed at a time; persistent signals cannot manufacture overlapping trades.
- The screening trade uses a 1 ATR stop, 1.5R target, and the live monitor's exact three-bar expiry.
- The expiry bar follows the production lifecycle: stop first, then expiry, then target. A candle touching stop and target is a stop.
- Fees, spread, and slippage total a conservative 34 basis points per round trip; the gate also requires positive mean return at doubled cost.
- Missing candles block or truncate affected outcomes. Right-censored trades at the end of a period are not scored.
- Strategy selection sees development and validation only. The holdout cannot change which rules were selected.
- Every rule's long and short sides are tested independently; a weak short side cannot hide a useful long side, or vice versa.
- A Bonferroni family-wise correction covers every directional hypothesis in both selection periods.
- Prefix-invariance tests prove that appending future candles cannot change a completed earlier result.

Archive evidence can reject a strategy, but it can never authorize a live alert. Public archives can be corrected later and cannot recreate historical order-book queues, transient spread, market impact, outages, funding, borrow, taxes, or the exact data vintage visible in real time. Any future survivor still needs a frozen forward shadow period and then paper trading with measured fills.

Short outputs are hypotheses only. Binance Spot does not make these configured products shortable; margin or derivatives require their own instrument identity, fees, funding, liquidation model, order-book history, and validation cohort.

## Software and live verification

- Complete Python suite: 1,001 passed.
- Opportunity audit plus affected live-engine tests: 48 passed.
- Live-monitor target: 139 Python tests, 13 native tests, and the deterministic protocol replay passed.
- Complete native Swift suite: 85 passed.
- Release bundle: engine manifest, source-tree binding, CycloneDX SBOM, ad-hoc hardened-runtime signature, helper startup, and real-window UI smoke checks passed. Developer ID notarization remains a separate release-credential step.
- Fresh packaged-helper observation: 900.241 seconds, 1,253 public BTC/ETH quotes, 30 contiguous finalized minute bars, two complete five-minute windows per asset, seven safe abstentions, 90 healthy heartbeats, zero validator issues, and clean exit code zero.
- Tracked-file and reachable-history secret scan passed.

The [live validation report](live-validation-2026-09-01.md) records the exact executable/source hashes, timing distributions, safety isolation, and event counts.

## Reproduce

```bash
make audit-day-trading AUDIT_END=2026-09-01T00:00:00Z
```

Bulk archives remain in the external macOS cache and generated JSON remains under ignored `build/`. This compact report and the code needed to reproduce it are committed. The source archives are described by [Binance's public-data documentation](https://github.com/binance/binance-public-data/blob/master/README.md); Binance also has a [documented example of an archive changing](https://github.com/binance/binance-public-data/issues/475), which is why this evidence is deliberately non-promotable.
