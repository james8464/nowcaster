# Whole-branch review — b1f0846..587d8b4

Reviewer /root/final_branch_review, astra/high. Ready: No; 4 Important, 0 Critical. Read-only, full package reviewed in passes; no full suites, app/study actions or historical searches. Focused diagnostic reproduced all six unavailable indicator features. Structural generated snapshot comparison found only cohort/protocol/outcome identities changed, no performance rows; existing Python–Swift parity passed. Not every test or historical-plan line was reviewed.

## Important findings

1. **The learning evaluator cannot evaluate its declared strategy-specific indicators.** `src/background_research/data.py:156`: preparation advertises `ema_12`, `ema_26`, `adx_14`, `donchian_upper`, `donchian_lower`, `session_vwap`, but rule evaluator receives raw bars plus only RSI and volume z-score. All such rules fail and consume retained attempts. Synthetic donchian failure is systematic, not ordinary unsuccessful research. Compute declared features causally using frozen definitions; test each feature/family and future-prefix invariance. Preserve failed attempts/immutable campaigns.
2. **Unexpected collector exit leaves Start permanently ineffective.** `PaperSessionCoordinator.swift:74`: active remains true after LivePaperSignalService.finish reports termination; UI switches to Start, but start returns immediately. Without learning status can remain Collecting. Propagate termination, stop/pause owned research, expose recoverable honest state. Test learning enabled/disabled, early contender exit and actual retry.
3. **Choose Research Folder changes only service, not retained binding.** `TradeDeskView.swift:36`: after Start/Pause A, importer opens B directly while preferences retain A. Next Start reselects A and fails identity. Default-folder view appearance also ignores saved custom source. Route explicit stopped-source selection through coordinator, preserve restore identity checks and old campaign evidence; no silent reset/repurpose.
4. **Notification helpers bypass shutdown ownership.** `LivePaperSignalService.swift:372`: reservation/status/outcome calls use static command without owner, detached task/raw Process. Shutdown drains only registered commands/collector; monitor cancellation does not drain helpers or prevent late outcome launch. Delivery rechecks cancellation, so unwanted delivery NOT proven. Track commands, close launch gate, settle/drain pending work within bounded lifecycle. Test during reservation and permission/delivery awaits.

## Accepted rulings / retained minors

Accepted lineage, lazy exports/preownership launch, new execution identities after STOP, result-before-checkpoint retention, explicit batch-completion, worker bounds, transaction/submission ownership, explicit capacity recovery, owned collector stop, preparation/expected hashes, isolated test storage/external observations, broken-output shutdown, stable native navigation/outer minimum/scrolling and exact immutable dummy exception. Reject interpretation of missing declared indicators as normal research failure.

Retain minor compressed shield, duplicate Unavailable text, horizontal inspector scrolling, headermap/AppIntents/XCTest warnings. Intermittent paint not independently eliminated; cold startup noticeable; trainer complexity later decomposition, not speculative defect.

## Declined to judge / remaining gates (none implied passing)

- Profitability/qualified trades/investment suitability.
- Protected frozen September8 study state/correctness.
- Broker/credentials/premium feeds/daemons.
- Historical strategy-search quality; no repeated searches authorized.
- Continuous healthy-feed/24-hour reliability; ten-minute interval predominantly stale by existing threshold.
- Sleep/offline hardware and long-duration recovery.
- 150-day production scale/capacity estimates.
- A completed evaluation/checkpoint after authenticated installed restore (resume/Pause identity only).
- Installed timed expiry with details already open.
- Installed corrupted-checkpoint visibility (Python/native propagation only).
- VoiceOver/measured contrast/Increased Contrast/Reduce Motion/Transparency.
- Independent visual approval of images/icons (reviewer read source/reports, controller inspected images).
- Entire current UI scheme all-green (initial failures/skips plus focused passes).
- Final signed parity/install/package acceptance.
- Distribution notarization/broader deployment.
- Remote push/terminal CI.
- Retained-campaign handover across runtime change: explicit preservation decision required.

Final fixes require one combined wave and one scoped re-review. New-source full verification, final signed install and remote CI remain release gates; prior1748Python/160native/installed acceptance predate finalownership fix, scoped122 does not replace them.
