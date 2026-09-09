# Live paper review — 9 September 2026

**Conclusion:** No demonstrated profitable strategy. The study has no closed trades and its only open position crossed missing-feed intervals. Use this evidence to improve accounting and diagnostics, not to tune the experiment or choose a winner.

## What was observed

Read-only snapshot of study `99583f768b689fe2e83bdba42cef0f68c1068cc793262d8f30fb783f947cd6ff`, whose fixed window is 8 September–7 December 2026. Both frozen candidates failed the historical screen. Independent 10,000 USDT paper accounts are not a combined portfolio.

At **05:57:34 UTC**:

| Observation | BTCUSDT | ETHUSDT |
|---|---:|---:|
| Decisions recorded | 4 | 0 |
| Entry fills | 1 | 0 |
| Closed trades | 0 | 0 |
| Open quantity | 0.02823 BTC | 0 |
| Net liquidation-marked P&L, USDT | -1.4606534428011 | 0 |
| Observed / expected minutes | 614 / 673 | 614 / 673 |

The collector was observing live data, PID 74773, in the original frozen checkout. It was left running. The report was under one minute old at inspection. Its persistent `ConnectionClosedError` described a prior outage; fresh feed events showed collection had recovered. There were 35 retained gap records and 59 missing wall-clock minutes per asset, unchanged from the earlier check. The first gap was after BTC entry. The cause (network, sleep or upstream service) is not established by these records.

The 01:00 UTC BTC entry was accepted. The 02:00, 03:00 and 04:00 decisions were rejected as `account_busy`: these are not three additional trades or three independent successful predictions. ETH's lack of a trade is not a profitable result or evidence that another rule should replace it.

## Findings tied to actual decisions

### Open-position contamination was not prominent enough

The underlying BTC position had `tainted=true`, but summary `tainted_trades=0` counted **closed** tainted trades only. That counter was not arithmetically wrong; it could be misread as evidence of uninterrupted observation. Missing prices can hide stop/target crossings and cannot be repaired by inventing retroactive fills.

Improvement: separately report open-position taint, frozen levels and expiry, stale valuation, and the closed-only count. Retain all gaps. No existing decision, balance or experiment rule is rewritten.

### Gross reward/risk obscures the cost burden

The following are the existing frozen paper plan, **not current trading recommendations**. Entry was at 01:00:01.019650 UTC, with expiry at 13:00 UTC. A read-only ledger snapshot at 06:01:19 UTC confirmed the retained values:

| Frozen / modeled quantity | Value |
|---|---:|
| Entry price, USDT/BTC | 78,814.04733 |
| Stop trigger bid, USDT/BTC | 78,124.4577538147462 |
| Target trigger bid, USDT/BTC | 79,848.43169427788070 |
| Full entry cost including entry fee, USDT | 2,227.1454766820259 |
| Model P&L if all remaining quantity exits at stop bid | -24.9991117286997 USDT |
| Model P&L if it exits at target bid | +23.5956952679578 USDT |
| After-cost reward / loss | 0.9438613469 |
| Additional frozen stress deduction | 7.56472989082806 USDT |
| Stressed stop / target P&L | -32.5638416195278 / +16.0309653771298 USDT |
| Modeled break-even exit bid | 79,011.3388799805 USDT/BTC |

The gross target distance is 1.5 times the stop distance, but that is not an after-cost payoff ratio. These conditional calculations do not estimate the likelihood of either outcome. Actual execution could be worse; the study uses public quote sizes, not guaranteed fills. Expiry, partial exits and gaps mean this is not a two-outcome probability model.

The current equity calculation **already includes hypothetical remaining exit fees and slippage**. Do not subtract them again from reported net P&L. The enhancement explains these economics; it does not lower cost assumptions to manufacture profit.

## Separate software defects found during review

These were reproduced with synthetic tests, not inferred from BTC's small loss:

- Strategy calibration and fold scoring multiplied already-directional portfolio returns by the short signal again. A losing short could therefore be labeled a win. This is separate from the long-only frozen paper collector; it does not explain this BTC position.
- Positive-bar-return labels were described as target-before-stop probabilities without measuring target/stop outcomes. The corrected label must remain ineligible for the live target-before-stop gate.
- Calibration quality and confidence-threshold selection reused fitting outcomes. The revised workflow separates fitting, threshold selection and chronological confirmation, purges delayed outcomes at boundaries, and accounts for threshold search multiplicity with an approximate bound.

These changes reduce misleading evidence. They are not a new profitable strategy, a validation of previous probabilities, or grounds to enable qualified alerts.

## Reproduce the retained observation checks

Source directory: `~/Library/Application Support/Nowcaster/ProspectiveStudies/study-001-20260908`. Read `summary.json` for timestamped totals, and use SQLite read-only mode for the ledger. Run queries inside one read transaction for consistency:

```sql
BEGIN;
SELECT seq, kind, at FROM journal ORDER BY seq DESC LIMIT 1;
SELECT at, json_extract(payload,'$.accepted') AS accepted,
       json_extract(payload,'$.reason') AS reason
FROM journal WHERE kind='decision' ORDER BY seq;
SELECT at, payload FROM journal WHERE kind='fills' ORDER BY seq;
SELECT json_extract(payload,'$.accounts') FROM state WHERE singleton=1;
ROLLBACK;
```

For each open position use exact decimal arithmetic. With remaining quantity `q`, previously realized net proceeds `p`, total entry cost `c` and frozen trigger bid `b`:

```text
exit_factor = 0.9995 × 0.999
total_trade_pnl_at_bid = p + q × b × exit_factor − c
break_even_bid = max(c − p, 0) / (q × exit_factor)
additional_stress = full_entry_notional × 0.0034
```

The entry fill is an append-only journal record. Mark-to-market P&L naturally changes after the report timestamp; the numbers above are not a live quote.

## Research boundary

Keep the current BTC/ETH universe and both frozen diagnostic rules unchanged. Future strategy research needs a separate recorded hypothesis and selection round, retaining losers and the total campaign count. No parameter search was conducted for this review. Favoring the asset or threshold that happens to look best in these few hours would reuse evaluation data as training data.

Independent calibration evaluation follows [scikit-learn's calibration guidance](https://scikit-learn.org/stable/modules/calibration.html); the risk of selecting apparently attractive results from repeated trials is discussed by [Bailey et al.](https://sdm.lbl.gov/oapapers/ssrn-id2507040-bailey.pdf). Neither supports a guarantee of trading profits.
