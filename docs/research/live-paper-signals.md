# Live paper signals

Live paper signals is a user-started, paper-only trend research service in the
native Mac app. It watches public Bitcoin and Ether prices on Binance spot
(`BTCUSDT` and `ETHUSDT`). It checks completed one-minute observations and shows
either a **Long research** posture (a rising-price hypothesis) or **Stand aside**.
It does not support short selling on this spot feed.

This is not proof of profitability. A pattern can fail, costs can erase an edge,
and a paper result does not establish that a real trade could have been filled.
There is no promised number of daily suggestions. A weak, stale or incomplete
result correctly remains Stand aside.

## Using the Mac app

1. Open **Strategy Lab → Live paper signals**.
2. Select **Choose Research Folder…** and choose a previously registered Research
   Round 2 folder containing its unchanged `protocol.json`. This binds collection
   to the already recorded rules and candidate selection; it does not create a
   new research campaign or erase earlier failures.
3. Select **Start**. Leave Nowcaster running and the Mac awake and online. The
   app contains its own paper-signal engine; the release app does not need Python
   or a source checkout. Collection does not run after quitting the app and does
   not automatically restart at login. Select the same folder again to resume.
4. Inspect **Collection**, **Provider health**, **Last source observation** and
   **Last evaluation**. “Warming up” means enough continuous recent data is not
   yet available. “Stale” or “Feed unavailable” means the service cannot support
   a current suggestion. An internet connection alone does not establish a
   healthy feed; Binance public data must also be available in your region.
5. Turn on **Notify me about new paper research** only if you want macOS
   notifications. Permission is requested at that point. Notifications are
   deduplicated, expire quickly, and open Strategy Lab evidence. They are not
   instructions to trade. Turning this off stops future scheduling; macOS may
   still show an already delivered notification. Foreground banners recheck your
   opt-in, running collector and fresh matching evidence before presentation.
   Clicking a notice opens its original retained evidence, even after restarting
   the app or choosing a different folder. This historical view does not start
   collection, change your selected folder or enable notifications. If the
   original evidence is unavailable, the app says so rather than substituting a
   newer suggestion. Always check current evidence.
6. Select **Stop** to end collection. **Show Evidence** opens the retained folder;
   **Recent evidence** displays up to 200 recent events. Earlier events remain
   on disk. macOS accepting a notification does not prove that anyone read it.

An entry zone is a range used by the research hypothesis; invalidation is the
price at which that hypothesis fails; the target is its hypothetical objective.
The current-posture view hides levels after expiry, feed failure or stopping.
Historical notification evidence retains its original levels and expiry, clearly
marked as historical and not a current suggestion. The app does not
track your holdings, open or close positions, send orders, connect to accounts,
or use broker credentials. Its future broker interface is disabled. Other app
features and the frozen prospective study are independent of this service.

## Evidence requirements

The registered round must already contain enough suitable causal observations
and candidate evidence to clear its fixed walk-forward evaluation. A newly
registered folder will normally collect data and stand aside for a substantial
period. Starting the service does not manufacture a qualified strategy. The
existing failed BTC and ETH diagnostic study cannot qualify this service, and
losses or gaps must never be removed to make results look better.

Only completed bars with checked source, receipt and availability times enter
the append-only observation ledger. Decisions only use information available
at their decision time. Duplicate conflicts, non-final bars, future timestamps,
missing continuity and stale data suppress new postures. A reconnect requires
continuous warm-up. Previously published decisions are not revised using later
prices (“repainted”). Source observations, decisions, health changes and
notification attempts/outcomes remain available for inspection.

## Research setup and command-line use

Researchers can register a separate round with the existing runner. Choose and
record the hypothesis, candidate budget and timing before collecting evidence;
see [Research Round 2](research-round-2.md). Never point this service at the frozen
`ProspectiveStudies/study-001-20260908` folder or modify its checkout.

For a source checkout, use its configured Python environment:

```sh
python scripts/run_live_paper_signals.py status --directory /path/to/registered-round
python scripts/run_live_paper_signals.py start --directory /path/to/registered-round
python scripts/run_live_paper_signals.py stop --directory /path/to/registered-round
```

The app supervises one owned collector; do not launch a second collector for the
same folder. Logs are retained in `paper-signal-app.log`, event history in
`signal-events.jsonl`, and notification delivery attempts in
`paper-notification-delivery.jsonl`. The fixed protocol identity must match on
every resume. Stop and investigate a rejected directory rather than resetting it.

The native app keeps a local notification-to-evidence index in
`~/Library/Application Support/Nowcaster/PaperNotificationIndex`. Each notification
identity is permanently bound to its original folder, protocol and candidate.
Opening that evidence is read-only; it never rewrites the research ledger.
