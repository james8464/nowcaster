# Experimental Opportunity Feed — 18 September 2026

## Outcome

The Live Monitor can display a narrowly scoped **Experimental — paper only** opportunity when current research is directional but remains unqualified. This is a research display, not an alert, trade instruction, simulated fill, or evidence that a strategy is reliable or profitable.

| Field | Meaning |
|---|---|
| Output | Long or short research posture; deterministic entry range, protective stop, targets, expiry, and the reasons qualification failed |
| Status | `experimental_paper_only: true`; `qualification_status: unqualified` |
| Data boundary | Only the verified contiguous tail of finalized bars and research available by the decision time (`available_at <= decision time`), paired with a fresh quote from the same provider, feed, and symbol |
| Eligibility boundary | Healthy continuous monitoring, directional available research, no-repaint pass, supportable short where relevant, and feasible deterministic levels |
| Non-trading boundary | No setup, lifecycle transition, notification, order, fill, readiness evidence, promotion, or profitability accounting |

## What can appear

The monitor first evaluates the normal qualified-alert rules. When that result abstains solely because promotion, calibration, portfolio/contextual evidence, or related qualification evidence is still incomplete, it can project an experimental opportunity instead. The retained qualification reasons explain why the item is not eligible.

The projection requires all of the following:

- Research is current, directional, passes the no-repaint check, and was available by the decision time (`available_at <= decision time`).
- Market monitoring is healthy and the bars are finalized, contiguous, and available by the decision time (`available_at <= decision time`).
- The research identity matches the live quote's provider, feed, and symbol.
- A short is supportable when the posture is short, including stock borrow availability where applicable.
- The fixed level policy can derive a feasible plan from the verified bar tail.

The levels are deterministic for the same verified inputs and policy. They are reference levels for inspection only; they do not predict price movement, promise an outcome, or imply suitability for any person or account.

## What cannot appear or happen

The feed fails closed. It emits nothing when market data is unhealthy, evidence or quotes are stale or were not available by the decision time, the signal is unavailable, no-repaint fails, bars are not finalized or continuous, provider/feed/symbol identities differ, a short is unsupported, an unknown contextual failure is present, or levels are infeasible.

An experimental item is always unqualified. It never creates a setup snapshot, lifecycle transition, notification request, broker order, fill, or forward-paper record. It cannot improve readiness, satisfy a promotion gate, or establish profitability. The separate qualified-alert path retains its own gates and remains notification-only; it also is not a profit guarantee or investment advice.

## Study separation

This display feature does not alter the frozen BTC/ETH prospective study: no source, ledger, losses, gaps, rules, end date, account state, or collected evidence is changed. It is a deterministic software projection from the monitor's current verified inputs, not a new strategy result or a replacement for forward validation.
