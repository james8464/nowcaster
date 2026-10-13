# Reversal Pro v3.0: timing assessment

On 13 October 2026, the installed TradingView desktop app showed the user's
hidden “Reversal Detection v3.0 - Real-Time Pro (Non-Repainting)” overlay.
Its source was available in a read-only editor, with an all-rights-reserved
notice. We did not edit, copy, publish, or enable that script.

The inspected code assigns its reversal label an earlier pivot-bar position
(`pivotBar`) after a reversal amount confirms the pivot. The pivot's stored
bar can itself come from an earlier bar offset by a confirmation-bar setting.
Thus the label can appear in historical view *before the bar on which the
reversal was actually knowable*. This is the key reason the chart may seem
exceptionally accurate. The observation does not establish whether an alert
issued in real time subsequently changes; that needs timestamped alerts
retained prospectively, with the chart left running.

For a fair test, compare each retained alert's creation time and price with
the first available subsequent bid/ask quote. Do not score trades from
retroactively placed historical markers. Preserve all alerts, missed
sessions, costs, and losses. No conclusion about after-cost profitability can
be drawn from the overlay's appearance alone.

TradingView's [repainting guidance](https://www.tradingview.com/pine-script-docs/concepts/repainting/)
explicitly discusses the risk of plotting into the past. The separate
[Nowcaster causal reversal hypothesis](nowcaster-causal-reversal-chart-strategy.pine)
instead places markers only on the confirmation bar; it is independently
written, unqualified research, not a licensed derivative of Reversal Pro.
