# Fixed trend strategy comparison

## Decision

The 4 October 2026 experiment completed. **None of the six candidates advances to a new paper study.** Three fixed trend rules were tested separately on Bitcoin and Ether: EMA/ADX trend, Donchian breakout and VWAP continuation. Every candidate lost on average after costs in both date windows. No parameters were changed after seeing results.

This is a new 15-minute research experiment, not a test of the installed app's complete one-minute advisory service. It does not install a strategy, change the running studies or turn on alerts. The earlier cost-aware advisor change remains source-only.

## Measured results

The later check covers **22 September through 3 October 2026, UTC**. Each row is a separate long-only simulation; trades across rows can overlap and must not be added into a portfolio return.

| Asset | Rule | Completed trades | Average before costs | Average after costs | Average with doubled costs | Net winning trades |
|---|---|---:|---:|---:|---:|---:|
| BTC/USDT | EMA/ADX | 29 | −0.226% | −0.566% | −0.906% | 24.1% |
| BTC/USDT | Donchian | 17 | −0.215% | −0.555% | −0.895% | 23.5% |
| BTC/USDT | VWAP continuation | 30 | −0.206% | −0.546% | −0.886% | 26.7% |
| ETH/USDT | EMA/ADX | 35 | −0.222% | −0.562% | −0.902% | 25.7% |
| ETH/USDT | Donchian | 21 | −0.259% | −0.599% | −0.939% | 28.6% |
| ETH/USDT | VWAP continuation | 43 | −0.087% | −0.427% | −0.767% | 34.9% |

These percentages are **average price returns per completed hypothetical trade**, not changes in the user's money. Base costs subtract 0.34% per round trip; stress subtracts 0.68%. They assume, per side, a 0.10% fee, 0.02% half-spread and 0.05% slippage. Historical bid/ask books and actual fills were not available.

The earlier screen, **9–21 September**, had 18–59 completed trades per candidate. Its gross averages were positive, ranging from 0.086% to 0.250%, but costs reduced every average to a loss, from −0.254% to −0.090%. In the later period even the gross averages were negative. Reducing fees alone would therefore not make these later outcomes positive at zero costs.

For the later period, maximum drawdown of each **closed-trade return index** was 9.05%–18.00%. This index compounds serial full-notional trade returns. It is not a cash-account simulation, does not mark intratrade exposure and is not the installed app's portfolio drawdown. Exact separate base/stress values are retained in the result file.

## Fixed method and data quality

The protocol was recorded before this experiment downloaded outcomes. It uses the existing configured indicator parameters, a two-ATR stop, three-ATR target and 24-bar maximum hold (six hours). ATR measures trailing price variability. Targets smaller than the stressed cost allowance are not traded. Each candidate holds at most one position. Spot data do not authorize short selling.

Signals use finalized bars and past information; entry is at the next continuous opening. The existing opportunity evaluator gives the stop precedence when a bar reaches both barriers, honors an adverse opening gap, and gives expiry precedence over a target on the final bar. These are specified modeling choices, not a reconstruction of historical order execution.

The source contains **54 checksum-verified daily archive files and 5,184 fifteen-minute bars**, including two warmup days per asset. Both scored windows have their full expected bars: 1,248 per asset in the screen and 1,152 in the later check. There are no missing bars, duplicate opening timestamps or invalid boundary rows in these files. Every trade's OHLC input passed the existing evaluator's validation.

The earlier screen retains **50 right-censored signal attempts** across four candidates. They lacked enough observations before the window ended; they were not fabricated into completed trades. These are attempts, not 50 independent open positions. The later window has no right-censored or gap-truncated attempts. Censoring blocks advancement under this protocol even if other metrics were favorable.

No candidate reached the predeclared 100 completed trades per period. All also failed positive base/stressed average and the adjusted daily lower-bound requirement. That daily statistic uses equally weighted exit-date averages and a one-sided Bonferroni adjustment across six candidates and two windows. Its distributional assumptions and small sample mean it is a diagnostic, not a calibrated probability of profitable trading.

## What this changes

We now have concrete evidence against deploying these particular 15-minute trend configurations. The result is not simply “too few trades”: all six also lost after modeled costs, and all six lost before costs in the later window. Whether different entry timing, exits or market selection would help is a **new hypothesis**, not a conclusion of this test. No best-looking loser was promoted and no variants were tuned on these results.

The dates overlap prices observed by other studies, so the later check is **not asserted to be untouched independent data**. Archive files can also be corrected after publication. Both windows must now be treated as development evidence. Future improvements require a separately recorded candidate set and genuinely future observations; these results do not justify copy-trading recommendations.

## Evidence and verification

- [Predeclared protocol](../../data/research/experiments/trend-check-2026-10-04/protocol.json)
- [Every candidate, both periods and verification receipt](../../data/research/experiments/trend-check-2026-10-04/result.json)
- [All archive checksums and availability receipts](../../data/research/experiments/trend-check-2026-10-04/source-manifests.json)
- [Reproducible analysis](../../data/research/experiments/trend-check-2026-10-04/analyze.py)
- [Official source documentation](https://github.com/binance/binance-public-data)

The separate arithmetic check reconstructed all **404 trade records** across the 12 candidate-period combinations, verified chronology and within-candidate non-overlap, and reproduced base/stress averages and closed-trade-index drawdowns. That count includes overlapping strategies and is not 404 independent market experiments. Eighteen sampled prefix checks found no signal changes when later data were appended; these are sampled causality checks, not proof against every possible implementation defect or source revision.

The full registration, runtime/source fingerprints, input bars, individual trades, original executed analysis and completion hash remain at:

`/Users/james/Library/Application Support/Nowcaster/CandidateCampaigns/trend-check-2026-10-04-attempt-001`

The checked-in analysis was formatted after execution; AST equivalence with the retained executed source passed. The evaluated application source was `093996a9493ca176fb1419f53345306725ee85f0`; CI run [37134180377](https://github.com/james8464/nowcaster/actions/runs/37134180377) succeeded. That software result is recorded for provenance, not counted as market evidence. No application source, installed environment or existing study was modified in this experiment.

Reproduction requires the pinned application source and existing Python dependencies, `PYTHONPATH=.` and the analysis script with a **new** output directory as its sole argument. Existing output directories are refused. Repeating the same market dates is a reproduction, not another independent test.
