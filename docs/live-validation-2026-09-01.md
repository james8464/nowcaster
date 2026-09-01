# Live market validation — 1 September 2026

## Post-audit final observation

After the direction-specific day-trading audit, its gap-safe ATR correction, and the final code-review hardening, the rebuilt packaged helper completed another credential-free 15-minute observation with **zero validator issues**. The UTC window was 22:16:14–22:31:40 on 1 September 2026 (crossing into 2 September in Europe/Paris).

- Packaged-helper SHA-256: `8f898112261d15e9c25a0e0b5c069f3fa906d713a773f0266d4adb90aa83f73b`
- Source-tree SHA-256: `ddb0a97a1241db416f5bb5d7419ff6fba8db2bf852957bc15154ce872d821e5c`
- Live duration after readiness: 900.241 of 900 requested seconds; startup took 14.998 seconds.
- BTCUSDT and ETHUSDT each produced 15 contiguous finalized minute bars and two complete five-minute decision windows, with zero bar gaps.
- The UI protocol received 1,253 quotes, 30 finalized bars, seven abstention decisions, 90 healthy heartbeats, and no reconnecting, stale, failed, or fatal event.
- Three decisions correctly waited for a post-finalization quote; four then reported `qualified_evidence_unavailable` because the isolated database contained zero qualified cohorts.
- No entry, stop, target, close, notification, order, or fill event was produced. No broker credential was supplied, `.env` loading was disabled, the child environment was allowlisted, and order submission was unavailable.
- Median provider-to-observer time was 129.751 ms for BTCUSDT and 131.440 ms for ETHUSDT; p95 was 142.525 ms and 144.423 ms respectively. These are one-machine observations, not latency guarantees.
- The helper shut down cleanly with exit code zero. Profitability was not assessed because no strategy qualified for an entry.

The detailed timing-fix observation below is retained as an earlier validation record. This post-audit run supersedes its executable/source hashes and event counts, while reaching the same safety conclusion.

## Outcome

The final packaged macOS helper completed a credential-free 15-minute Binance observation with **zero validator issues**. BTCUSDT and ETHUSDT stayed healthy, produced contiguous finalized bars, respected causal time ordering, abstained without qualified evidence, and shut down cleanly. The reconnect loop found in the previous build is fixed.

This validates the public crypto feed, timing controls, bar finalization, decision scheduling, fail-closed behavior, persistence, native helper packaging, and shutdown path for the observed window. It does **not** validate profitability. The isolated database intentionally contained no qualified strategy cohort, so no entry, exit, stop-loss, or take-profit alert was permitted.

## Test scope and safety

- Final packaged engine: `build/Nowcaster.app/Contents/Helpers/nowcaster-engine`
- Engine SHA-256: `6e20bd34c4016a6469850f7ac9e0dd71d4bb5d7cde3b4d200df4362a3e63bc8d`
- Source-tree SHA-256: `357777508dbbf2918b6931d282de971abbf4920b7faf1feedeb05a4d648c6956`
- Window: 16:01:52–16:17:00 UTC, with 900.162 of 900 requested seconds after readiness
- Assets: public Binance spot BTCUSDT and ETHUSDT
- Broker credentials supplied: no
- Parent environment: reduced to a non-secret allowlist; `.env` loading disabled in both parent and child
- Database: new isolated DuckDB file
- Order submission: unavailable and unused
- Notifications, order intents, orders, and order events recorded: zero

US markets were open during the work, but no Alpaca paper or live credentials were present in the environment or macOS Keychain. The stock connector was therefore excluded instead of weakening credential isolation or claiming an unperformed test.

## Results

| Check | Final post-review packaged helper |
|---|---:|
| Startup to ready | 14.242 s |
| Live observation | 900.162 of 900 requested s |
| Quotes delivered to the UI protocol | 1,486 |
| Finalized one-minute bars | 30 |
| Bar gaps or duplicate minute grains | 0 |
| Healthy subscribed events | 1 |
| Healthy heartbeats | 90 |
| Reconnecting, stale, failed, or fatal events | 0 |
| Complete five-minute decision windows | 2 per asset¹ |
| Decision records | 8, all `abstain` |
| Actionable decisions or notifications | 0 |
| Validator issues | 0 |
| Exit code | 0 |

¹ The run began partway through a fixed five-minute bucket, so that incomplete bucket was discarded. Only complete finalized decision windows counted, which is the intended no-repaint behavior.

The helper's median provider-to-observer time was 12.197 ms for BTCUSDT and 13.937 ms for ETHUSDT; p95 was 135.158 ms and 136.954 ms respectively. These are observations on this Mac and network, not an exchange latency guarantee.

During the run, Binance's quote clock read about 63 ms ahead of the Mac at the median. The engine preserved the original timestamps, waited until processing was causal, and then delivered each event to the observer in a median 3.002 ms for BTCUSDT and 2.761 ms for ETHUSDT after processing. No event was processed before its provider or local receipt timestamp.

## Persisted data audit

The final isolated database contained 42,198 raw market events, 30 finalized bars, eight abstention decision records, and 91 healthy status records. The UI-protocol observation log contained exactly 1,616 strictly sequenced records.

- All 42,198 event IDs and source event IDs were unique.
- Both assets had exactly 15 distinct minute bars spanning 16:01–16:15 UTC, with no duplicate grain and no gap.
- The largest observed provider clock lead was about 65 ms, inside the one-second safety bound.
- No processing timestamp preceded provider availability or local receipt.
- Every decision status was `abstain`: four records reported `qualified_evidence_unavailable`, and four correctly waited for a post-finalization quote.
- Setup, transition, notification, broker intent, order, execution-observation, readiness-receipt, and forward-evidence tables were empty.
- The data-quality issue table was empty.
- The monitor session ended as `stopped` with terminal reason `monitor_stopped`.

## Problems found and fixed

The previous packaged baseline received no quotes or bars and repeatedly alternated between reconnecting and stale health states. Direct reproduction showed that an ordinary provider/local clock offset caused strict timestamp validation to reject valid Binance events.

The fix:

- preserves provider, receipt, and processing timestamps instead of rewriting evidence;
- permits at most one second of provider clock lead, then waits for local time to catch up;
- fails closed if the lead is larger, the local clock regresses, or time does not settle;
- carries actual processing time through minute and aggregated bars;
- rechecks quote freshness after evidence computation, so queued or slow work cannot authorize an old entry;
- labels delayed stop and target observations using actual processing time;
- measures drift latency from provider time to actual processing time, preventing clock offset from hiding queue delay;
- adds a bounded live validator that rejects sequence gaps, stale tails, bar gaps, provider interruptions, unexpected actionable output, stderr, forced shutdown, or non-zero exit.

An independent review then identified four ways the validation guarantee could be stronger. The final version also:

- removes broker variables from the child environment and disables project `.env` loading during the probe;
- accepts the `packaged` label only for the exact `Nowcaster.app` helper after manifest, source-tree, executable-hash, and macOS signature verification;
- validates every observed wire envelope and market payload against the strict schema and exact Binance spot identity;
- permanently latches one process-wide causal clock failure, propagates provider-task exceptions immediately, and persists terminal state at the last safe watermark;
- authenticates explicit contextual-evidence start and expiry times, limits their window to 24 hours, and rechecks expiry after slow inference work;
- rejects an early helper exit even when it is clean and otherwise produces enough events to satisfy duration-scaled thresholds.

## What remains before risking money

The app is **not money-ready from this result alone**. Its active database has zero authenticated qualified cohorts, zero forward evidence, and zero readiness receipt. A reliable feed is necessary, but it says nothing about whether a strategy has positive net edge.

Before any real-money use, each asset/feed/timeframe cohort still needs the full research pipeline, untouched walk-forward and final holdout results after costs, a sufficiently long paper-forward period, execution/slippage calibration, and a fresh readiness receipt. The existing gates require at least 90 crypto days or 60 equity sessions, at least 100 closed paper trades, and the configured robustness, calibration, drift, and economic-evidence thresholds. Even after those gates pass, size should begin at the smallest practical level with broker-side risk limits and ongoing paper/live divergence monitoring.

## Reproduce

Source engine:

```bash
python -m scripts.validate_live_monitor --seconds 900
```

Exact packaged helper:

```bash
python -m scripts.validate_live_monitor --seconds 900 \
  --engine build/Nowcaster.app/Contents/Helpers/nowcaster-engine
```

Each run writes `summary.json`, `observations.jsonl`, and an isolated `monitor.duckdb` below a new ignored directory in `build/`. These potentially large raw artifacts remain local; this report records the durable, reviewable findings.
