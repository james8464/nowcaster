# Native interface review — 28 September 2026

Scope: current installed Trade Desk, its real-window XCTest interactions, and
the application/menu-bar lifecycle. This is not a profitability assessment or a
claim that every screen or Apple HIG provision passes.

## Fresh evidence

[Installed Trade Desk before redesign](01-installed-trade-desk-before.png) was
captured by the explicitly authorized XCTest runner today, after activating the
installed app. It shows retained public-data research stopped, not a live trade.
The image was visually inspected before inclusion; raw test recordings remain
local and are not committed.

Local result: `/tmp/nowcaster-ui-pending-20260928.xcresult`.
Installed app launch, Trade Desk controls, stopped collection, stand-aside state
and normal Quit passed. Synthetic historical display failed at disclosure
expansion; the test clicked inside the accessibility frame's leading inset.
The screenshot places the actual arrow 27 points from that frame's leading edge.
The subsequent suite verified the corrected expansion. No historical outcome
or publication requirement was rewritten to make the test pass.

Second result: `/tmp/nowcaster-ui-suite-20260928.xcresult` (retained failure).
Eight tests: four passed, three explicitly skipped, one failed with two recorded
issues (query and cleanup). All 12 sidebar destinations, stable monitor table
identifiers, installed launch/normal Quit, and stopped-by-default Trade Desk passed.
The completed-history query applied `value CONTAINS` to all AX element types.
The system log at 12:48:08 local records “Can't use in/contains operator with
collection 228 (not a collection)”; the XCTest evaluator raised a Foundation
exception and then timed out. The app's main thread remained in its normal
event loop. The test now checks only string values client-side. Live collection
and soak opt-ins were intentionally not repeated.

Targeted verification: `/tmp/nowcaster-ui-history-query-20260928.xcresult`, one
test passed in 90.806 seconds. The [expanded synthetic history capture](02-synthetic-completed-history-not-live-evidence.png)
was visually inspected: two retained BTC target outcomes appear under historical
hypothetical outcomes, while current context is unavailable, collection stopped,
notifications off, and the live view stands aside. The test also confirms no
current entry zone and byte-for-byte preservation of the lifecycle ledger.
**These are synthetic UI fixtures, not market-performance evidence.**

Fresh native unit suite: 128 Swift Testing tests plus 4 XCTest checks passed.
Final real-window result: `/tmp/nowcaster-ui-final-20260928.xcresult`, exit 0;
eight cases, five passed, three intentionally skipped, zero failures in 261.133
seconds. The opt-in skips are the already-completed live collection and soak,
and the separate pre-upgrade Quit helper. Installed launch/normal Quit and
synthetic completed-history display both pass. This closes the two pending
XCTest checks from September 27; it does not verify profitable qualified entries,
real notification banners, or the unimplemented background redesign.

The installed app's strict/deep signature verifies. No production code or
installed bundle was changed in this test-repair checkpoint. Both failed result
bundles and the successful retries remain retained locally.

CI 36356414974 for commit `00df7a0` completed successfully, independently checked
today: https://github.com/james8464/nowcaster/actions/runs/36356414974.

## Findings

| Priority | Observed issue | User impact | Design response |
| --- | --- | --- | --- |
| High | Twelve sidebar destinations plus a large slogan, controls, settings and long instructional boxes compete on the opening screen. | Hard to identify current status or the next useful action. | Four everyday destinations; advanced tools grouped; compact status and one primary session control. |
| High | Menu-bar code reads the older `liveMonitor`, while Trade Desk owns `livePaperSignals`. | Menu status/pause cannot be relied on to control the desk collector. | One session coordinator and consistent app/menu status; test window closure and Quit. |
| Medium | Setup actions occupy the main trading surface permanently. | Repeated setup competes with recurring observation/review. | Contextual setup sheet and secondary commands; retain keyboard/menu access. |
| Medium | Successful calendar import uses the same orange warning-triangle styling as failures. | Success looks like an error and creates needless alarm. | Distinct neutral success, recoverable issue and failure presentations; no trading-quality implication. |
| Medium | Collection, current decision, historical outcomes and strategy instructions share long vertical cards. | Current versus historical evidence takes extra reading and scrolling. | Short asset rows, selected-asset details and a dedicated historical view. |
| Medium | Background research and data prerequisites are not expressed as one session state. | A running process may be mistaken for active learning or qualified signals. | Explicit collecting/researching/waiting/paused/blocked states with meaningful counts and reasons. |

These are findings about this captured flow, not a numeric whole-app quality score.

## Bounded test-repair review

Independent read-only reviewer `ui_test_fix_review` found no Critical or
Important issues in the test-only diff. Two Minor maintenance risks are retained:
the observed 27-point chevron offset may change with AX/layout versions, and
whole-window per-element reads can be slower than a scoped snapshot query.
Neither weakens the assertions; the expanded-state check detects a missed click.
Avoid additional test rewrites until an observed failure or the separately
approved redesign supplies a reason to change the interaction.

Reviewer exclusions and executor disposition: the final full-suite result must
be verified locally; different display/OS accessibility layouts remain untested;
live collection and calendar semantics use the earlier retained acceptance
evidence, not this synthetic test; trading quality and the new redesign are not
judged by this test repair. No untested behavior is counted as passing.

## HIG applicability and verification register

Reviewed the HIG's six top-level catalogues and component groups relevant to the
Mac interface. Read applicable core pages through Apple's documentation data
endpoint when the web extractor returned only a JavaScript placeholder. Full
HIG-wide reading and conformance have **not** been completed.

| Area | Current evidence / disposition |
| --- | --- |
| Purpose, simplicity, visual hierarchy | Issues above; [principles](https://developer.apple.com/design/human-interface-guidelines/design-principles) and [layout](https://developer.apple.com/design/human-interface-guidelines/layout) guide the redesign. |
| Mac window and multitasking conventions | Installed native window and normal Quit verified; close-window continuation and multiwindow behavior not yet verified. [Windows](https://developer.apple.com/design/human-interface-guidelines/windows), [multitasking](https://developer.apple.com/design/human-interface-guidelines/multitasking). |
| Sidebar and navigation | Native sidebar exists; excessive competing destinations in this flow. [Sidebars](https://developer.apple.com/design/human-interface-guidelines/sidebars), [split views](https://developer.apple.com/design/human-interface-guidelines/split-views). |
| Toolbar, menus and disclosure | Native controls exist; shared status/control and disclosure interaction require verification. [Toolbars](https://developer.apple.com/design/human-interface-guidelines/toolbars), [menu bar](https://developer.apple.com/design/human-interface-guidelines/the-menu-bar), [disclosure controls](https://developer.apple.com/design/human-interface-guidelines/disclosure-controls). |
| Content grouping and tables | Large text-heavy boxes need reduction; verify concise, resizable asset/history rows. [Boxes](https://developer.apple.com/design/human-interface-guidelines/boxes), [lists and tables](https://developer.apple.com/design/human-interface-guidelines/lists-and-tables). |
| Settings and background status | General preferences should not crowd the main workflow. Background progress must distinguish activity from measured completion. [Settings](https://developer.apple.com/design/human-interface-guidelines/settings), [loading](https://developer.apple.com/design/human-interface-guidelines/loading), [progress](https://developer.apple.com/design/human-interface-guidelines/progress-indicators). |
| Accessibility and appearances | AX output captured, but keyboard-only, VoiceOver, enlarged text, contrast, Reduce Motion/Transparency and post-redesign dark/narrow layouts remain unverified. [Accessibility](https://developer.apple.com/design/human-interface-guidelines/accessibility), [Dark Mode](https://developer.apple.com/design/human-interface-guidelines/dark-mode). |
| Learning claims | Apple's [machine-learning guidance](https://developer.apple.com/design/human-interface-guidelines/machine-learning) was read, including inputs, feedback, mistakes, corrections, confidence, attribution and limitations. It supports explaining evidence and limits instead of displaying unvalidated confidence percentages. Research qualification remains a separate statistical question. |
| Keyboard conventions | Read [physical keyboards](https://developer.apple.com/design/human-interface-guidelines/keyboards), including standard/custom shortcuts and Full Keyboard Access. Ordinary Quit is verified; comprehensive keyboard-only navigation remains unverified. |
| Other applicable details | Typography, colors/materials, symbols, writing, charts, help/onboarding, notifications, privacy, input/focus and other rendered controls still need the detailed implementation audit. |
| Other-platform/absent capabilities | VisionOS volumes, watch controls, tvOS focus, games, payment flows, camera, health and similar absent features are not applicable to this native paper-research flow. Reassess if added. |

## Next checkpoint

The user approved the redesign direction. The written specification is at
`docs/superpowers/specs/2026-09-28-simple-desk-background-research-design.md` and
awaits its own review before implementation planning. No redesigned product UI
or background-learning service has been implemented in this review.

## 30 September — installed simplified desk acceptance audit

This dated addendum supersedes the old implementation-status paragraph above,
not its retained before/failure evidence. Scope: signed installed Nowcaster,
Trade Desk → selection → Settings → keyboard navigation to Research. No Figma
or browser artifact was created. Product Design audit context preflight found
no saved context. Native XCTest captures were explicitly authorized; no CUA
inspection or UserNotificationCenter access was used.

Fresh saved screenshots 03–09 were inspected as exact files. They show actual
installed windows with marked isolated acceptance data, not live performance.
The complete XCTest scheme retained two failures (fixture identity and premature
geometry assertion); these images are successful captures, not a claim that the
whole first run passed. The corrected appearance matrix passed independently.

| Capture | Observed finding | Limit |
| --- | --- | --- |
| [03 light minimum](03-installed-minimum-light.png), [04 dark minimum](04-installed-minimum-dark.png) | Four everyday destinations, collapsed Advanced, prominent Start, both assets, setup/data/help remain legible at 820×620 points. | Toolbar shield is compressed; duplicate “Unavailable · Unavailable” copy is a retained Minor issue. No contrast-ratio measurement. |
| [05 light selection](05-installed-selection-light.png), [06 dark selection](06-installed-selection-dark.png) | Selected-asset detail remains reachable and legible at minimum width, with stand-aside and unavailable evidence instead of invented entry levels. | Isolated empty-data state, not a qualified signal or profitability evidence. |
| [07 Settings](07-installed-settings.png) | Learning, app-open resume, login and notifications are separate; resource explanation and close-window/Quit language are visible. | Login and notification toggles stayed off; no system permission flow exercised. |
| [08 blocked synthetic identity](08-synthetic-identity-blocked.png) | Needs-attention reason is visible; failed fixture A does not display entry levels. | Retained failure, not successful synthetic training. |
| [09 keyboard Research](09-installed-keyboard-research-dark.png) | Arrow navigation reached Research; resource profile and five-worker limit are visible. | Schedule is below the captured viewport; this image alone does not prove lower-content access. |

### Applicability and verification updates

| Area | Evidence and disposition |
| --- | --- |
| Mac hierarchy/navigation | Installed captures and existing full-route XCTest checks cover four primary routes plus Advanced, selection, setup and Settings. No new cosmetic sweep was made. |
| Window resizing/appearance | Light/dark, minimum/default sizes and app-controlled enlarged text passed the focused appearance matrix after waiting for actual requested geometry and hittability. Original premature assertion/video retained. |
| Keyboard/accessibility | Actual sidebar arrow navigation, Command-comma Settings and normal Command-Q were exercised. AX labels and values were checked separately. These are **not** a VoiceOver usability test or exhaustive keyboard-only audit. |
| Assistive/system settings | VoiceOver, Full Keyboard Access, system contrast, Reduce Motion, Reduce Transparency and alternate display hardware were **not tested**. No blanket accessibility/HIG-conformance claim. |
| Status/evidence semantics | Empty/stale, stand-aside, blocked identity and retained progress are visible. Separate installed lifecycle/training checks passed as bounded below; an image alone cannot prove a worker ran. |
| Notifications/privacy | Optional permission remained off. Real banners, delivery and OS permission UI are deliberately out of scope. No account or order flow exists in this acceptance. |
| Other platforms/capabilities | Other-platform HIG components and absent payment/camera/health/game capabilities remain not applicable. |

This is an evidence-bounded flow audit, not a numeric whole-product score or
full HIG certification. The two Minor visual/copy issues above are retained;
actual installed background lifecycle outcomes belong in the Task6 report.

### Real installed closed-window lifecycle evidence

The corrected real XCTest passed758.299s, including12:11:19–12:21:19Z with no
window, menu reopen, Pause, normal Quit and stopped-by-default relaunch.
Both assets retained10 new minute receipts, no missing provider minutes in that
interval; prior ledger prefixes and protocol bytes stayed unchanged. Exact
collector/worker identities stayed constant. This is collection/lifecycle
evidence, not actual strategy-training progress: real history is insufficient.

| Fresh saved capture | Observed finding / limit |
| --- | --- |
| [10 real Waiting](10-installed-real-waiting.png) | Full “Waiting for eligible data”, actual eligible-observations reason, zero attempts/failures and no checkpoint. No fabricated training. Lower resources/schedule require scrolling. |
| [11 reopened stale](11-installed-reopened-stale.png) | Same installed window reopened; both assets correctly show stale, stand aside and no entry levels. The original XCTest attachment name says “fresh”, but this image **does not** prove fresh state. Fresh post-close receipts are established independently by retained timestamps. |
| [12 Pause and selection](12-installed-paused-selection.png) | Selected ETH detail reflects stopped collection, Start action and stand-aside; no stale levels remain shown. |
| [13 default relaunch](13-installed-relaunch-stopped.png) | Ordinary relaunch is Not started with Start and both assets stopped; no automatic session launched. |

The unchanged15s protocol causes transient resource pauses between1-minute
receipts. Read-only samples measured91.823s running versus508.177s paused during
the600s interval. Collection continued throughout. Retained usability concern:
automatic research pause says “Paused” while the whole-session action remains
“Pause”; README explains the distinction. No freshness gate was relaxed to make
the acceptance look continuously green. Actual VoiceOver, system contrast/motion
settings, and an installed qualified detail expiring while already open remain
unverified; expiration/stand-aside presentation has native unit coverage.

### Marked synthetic native training / restoration

Installed XCTest passed191.733s on a separate marked fixture, not the real desk.
Actual native worker advanced while closed from zero to50 reserved attempts and
checkpoint6; retained results include4 completed evaluations,1 failed candidate,
11 duplicates and34 interruptions. No qualification/performance conclusion.
Normal active Quit completed2.200020s, all installed helper descendants exited,
and an unrelated sentinel remained alive. Opted-in relaunch authenticated a new
execution for the same campaign/runtime/unfinished batch and retained evidence.
The test paused immediately afterward: **post-restore training progress was not
observed**. Restore opt-in was reset off and the app left stopped.

| Fresh saved capture | Observed finding / limit |
| --- | --- |
| [14 synthetic checkpoint](14-synthetic-checkpoint-pausing.png) | Research displays50 retained attempts,1 failure and checkpoint6 during Pausing. Hash wraps rather than clipping, but is technical/long; no cosmetic change made. This is explicitly synthetic control-flow evidence. |
| [15 authenticated restore](15-synthetic-authenticated-restore.png) | Restored session displays Waiting with stale stand-aside cards. Ownership and retained batch identity are independently verified on disk; this capture is not evidence of a new completed training evaluation. |

All added saved images03–15 were individually inspected before this addendum.
The audit skill drove numbered fresh evidence, applicability and honest limits;
it did not justify changing system settings or claiming untested accessibility.
