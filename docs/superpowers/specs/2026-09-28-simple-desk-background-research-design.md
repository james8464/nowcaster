# A simpler Mac app with visible background paper research

Date: 28 September 2026
Status: written specification and implementation plan approved by the user on 28 September 2026; implementation in progress.

## Intent and boundaries

The user wants an understandable native Mac app that observes supported markets,
explains rule-based trading setups, tracks their paper outcomes, and continues
research when its window is closed. The immediate complaint is clutter, not a
request to hide failed results or loosen trade-publication requirements.

Success means fewer competing actions, clear current-versus-historical evidence,
and a controllable background session with durable progress. Success does **not**
mean a required daily trade count or a profitable backtest obtained by repeatedly
trying until one passes. Research can end with no qualifying strategy.

Keep SwiftUI/AppKit, the installed `.app`, Dock presence, and the existing branch.
No broker connection, credentials, orders, automatic notification opt-in, or new
OS daemon. Never touch the frozen September 8 study, its checkout/environment,
protocol, campaigns, records, positions, gaps, or losses. Separate new research
from both that study and the currently registered paper-desk protocol.

## Observed baseline

Fresh installed-app XCTest capture on September 28 shows 12 sidebar destinations,
a large promotional heading, four adjacent setup/action buttons, duplicated
status explanations, a notification setting, and long instructional boxes above
and below the decision section. Much of the window is instructional text instead
of a concise answer to what is happening now.

Code inspection found that `LiveMonitorMenu` reports `model.liveMonitor`, whereas
Trade Desk uses `model.livePaperSignals`. The menu cannot currently be treated as
a reliable control for the desk. The app already has a `MenuBarExtra`, login-item
support, a retained-data search engine, checkpoints and thermal controls; build
on those components rather than introduce another independent engine.

The installed launch/normal-quit and historical-outcome acceptance tests passed
on September 28. Two test-only issues were corrected: a click in the chevron's
leading inset and a string predicate applied to numeric accessibility values.
The final UI suite passed all five enabled cases (three intentional opt-in
skips). No trading behavior changed. Failed attempts and the detailed limits
remain in `.superpowers/audits/2026-09-28-interface-review/report.md`.

## Chosen approach

1. **Recommended and selected:** simplify the native shell, preserve advanced
   tools, and give existing collector/research services one explicit lifecycle.
2. A cosmetic-only pass is smaller but leaves competing monitors and unclear
   background behavior unresolved.
3. A full rewrite or menu-bar-only app creates unnecessary migration risk and
   conflicts with the user's request for a normal installed Mac app.

## Everyday interface

- Primary navigation: **Trade Desk, Markets, History, Research**. Group existing
  specialist screens under a collapsed **Advanced** section; retain their
  navigation targets and keyboard/menu access. Do not delete research features.
- Trade Desk starts with a compact session status and one primary **Start/Pause**
  action. A paused session retains records. Setup, folder selection, calendar
  import and evidence export move to an appropriately labelled secondary menu or
  setup sheet, with standard menu-bar equivalents.
- Show concise rows for supported assets: source freshness, observed trend,
  experimental setup or **Stand aside**, and the most important reason. A row
  opens a detail inspector containing entry/invalidation/target/expiry only when
  the existing publication checks permit them. No invented confidence score.
- Keep a short, persistent **Paper research** label. Detailed risk, methodology,
  data coverage and strategy explanations remain one action away. Missing or
  expired data remains prominent; simplifying the UI must not hide it.
- Clearly separate connected markets from cached/demo/imported research. The
  current desk covers BTC/USDT and ETH/USDT Binance spot, long/stand-aside only.
  Do not imply stock, oil, futures or short-selling support for this desk.
- History shows retained completed hypotheses, costs and available outcomes with
  dates and simulation labels. Never render expired historical levels as current.
- Research shows the active batch, attempts, failures, resource state, last
  checkpoint and why it is running, paused, waiting or blocked. Full trial details
  are available on demand rather than filling the home screen.

## Background-session lifecycle

Introduce an app-owned session coordinator, independent of window `.task` lifetime,
that supervises the selected paper collector and optional research worker. Avoid
duplicate processes using existing directory locks plus explicit child ownership.

Start/Pause applies to the whole paper session. Background learning is a separate
explicit opt-in in setup/settings; enabling it does not place orders or enable
notifications. Starting the session starts collection and makes eligible research
work available. Pausing stops new dispatch and checkpoints in-flight work; data
collection stops without erasing records. UI states distinguish pausing from
paused until children acknowledge completion.

Closing or hiding the last window leaves the explicitly started session running.
The normal Dock icon remains. Dock reopen and **Open Nowcaster** bring back the
window without starting a second session. The menu-bar extra is user-controlled
and reflects the same session state, with Open, Start/Pause, research status and
Quit; no separate misleading older-monitor status.

Quit means checkpoint and stop all app-owned jobs, with bounded shutdown and an
honest interruption record if orderly completion fails. Never stop unrelated or
frozen-study processes. Relaunch defaults to stopped unless the user separately
enabled resume-on-launch. Revalidate source/protocol/checkpoint identity before
resuming. Login startup remains opt-in through Apple's existing service API.
Do not change system sleep settings: no local collection/computation occurs while
the Mac is asleep or offline, and missed intervals stay recorded as gaps.

## Continuous research without repeated-test self-deception

Use the existing candidate grammar, chronological replay and Deep Research
checkpoint/resource primitives. Add a scheduling layer, not self-modifying code
or a free-running loop that overwrites the active strategy.

- Register a separate learning campaign before evaluating candidates. Freeze its
  assets, strategy families, parameter bounds, cost model, data eligibility,
  batch budget, schedule and selection criteria. Retain every trial and failure.
- Use supported, provenance-checked finalized observations; never infer historical
  availability from present-day downloads. Reuse existing causal/no-repaint
  contracts, next-observation execution and conservative cost/stress models.
- Default to at most one new scheduled batch per asset per UTC day, a maximum of
  100 candidate attempts per batch, and require a new eligible data fingerprint.
  Training windows may advance only under the campaign's registered schedule.
  Resume an interrupted batch under its original identity; do not count it as a
  new independent campaign or reset its losses/trial count.
- Repeated training search may adapt candidates on training data only. Reserve
  validation and final-test boundaries in advance. A final holdout is evaluated
  once for a locked candidate; after inspection, it cannot qualify later tuned
  candidates as unseen. Link campaigns and holdout usage in the retained registry.
- Preserve all current evidence/coverage/trade-count gates. Learning must wait
  when eligible history is insufficient, exhausted, stale or missing; it must not
  manufacture calendar coverage or lower sample requirements.
- A research winner is a proposal for a **new, separately registered prospective
  paper evaluation**, never a silent replacement of an active/frozen strategy.
  Report gross/net performance, sample size, drawdown and failed checks together.
  A simulated return is not proof of executable real-money profitability.

## Resource and failure handling

Default to efficient background work: half the logical cores, reserving at least
two where available, with one numerical thread per worker. Keep explicit manual
performance controls in Research. Pause dispatch on serious/critical/unknown
thermal state, low-power mode, memory/disk pressure or an unhealthy live collector.
Resume automatically only after a resource-triggered pause recovers, not after a
user pause. Prefer progress/events over frequent polling or busy waiting.

Network failures back off and preserve stale/stand-aside behavior. Bad identity,
malformed state or a held directory lock blocks safely with a recovery explanation.
Disk errors never silently discard trial records. No notification for every
routine attempt; meaningful failures remain visible without repetitive alerts.

## Apple HIG review and acceptance

Apple's HIG is guidance across multiple platforms and capabilities, not a single
universal checklist. Do not claim Apple certification or compliance with every
page. Maintain an applicability register: **verified / issue / not applicable /
not yet tested**, with evidence for each applicable item. Reading a guideline or
using a native control does not itself demonstrate compliance.

The current review covers the HIG catalogue and initial Mac-relevant guidance:
[principles](https://developer.apple.com/design/human-interface-guidelines/design-principles),
[macOS](https://developer.apple.com/design/human-interface-guidelines/designing-for-macos),
[layout](https://developer.apple.com/design/human-interface-guidelines/layout),
[sidebars](https://developer.apple.com/design/human-interface-guidelines/sidebars),
[toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars),
[disclosure](https://developer.apple.com/design/human-interface-guidelines/disclosure-controls),
[windows](https://developer.apple.com/design/human-interface-guidelines/windows),
[multitasking](https://developer.apple.com/design/human-interface-guidelines/multitasking),
[settings](https://developer.apple.com/design/human-interface-guidelines/settings),
[accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility),
[menu bar](https://developer.apple.com/design/human-interface-guidelines/the-menu-bar),
[loading](https://developer.apple.com/design/human-interface-guidelines/loading),
and [progress](https://developer.apple.com/design/human-interface-guidelines/progress-indicators).
Complete the relevant typography/color/dark-mode/materials, keyboard/VoiceOver,
charting, notifications, writing, privacy and machine-learning sections during the
detailed implementation audit. This is not a claim that all HIG pages were read.

Acceptance must include:

1. Fresh before/after real-app captures at normal and narrow sizes, light/dark
   appearances; no hidden critical action or clipped important label.
2. Native window controls, resizable split views, normal Settings and menu/keyboard
   access. Content-first hierarchy; no redundant promotional headline.
3. Keyboard-only navigation, sensible focus/VoiceOver labels, non-color status,
   contrast checks, larger-text accommodation, Reduce Motion/Transparency checks.
   Record genuinely untested assistive-technology behavior as unverified.
4. XCTest for setup, folders/calendars, fresh/stale data, details/history,
   Start/Pause, close-window continuation, menu/Dock reopen, Quit, opt-in resume,
   duplicate prevention and the installed bundle. Synthetic outcomes stay clearly
   marked and isolated; a synthetic pass never qualifies trading evidence.
5. Deterministic coordinator tests for checkpoint recovery, no work without new
   eligible data, budgets, resource pauses, user-pause precedence, source mismatch,
   held locks, interruption/gap retention and no holdout reuse or implicit promotion.
6. A bounded real public-feed background soak requiring new timely receipts for
   both assets after window closure; verify stop and ledger-prefix preservation.
   Do not claim 24/7 reliability from a short test.

## Delivery

After written-spec review, produce the implementation plan and select execution.
Keep all changes on `feature/research-round-2`; preserve user work. Commit/push
verified code and documentation, build/sign the app, replace the installed app
only after an ordinary quit with a recoverable backup, and verify the installed
window. Report remaining data/qualification/HIG limitations explicitly. Do not
repeat completed historical strategy searches or alter the frozen study.
