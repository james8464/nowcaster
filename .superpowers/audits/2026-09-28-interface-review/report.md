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
