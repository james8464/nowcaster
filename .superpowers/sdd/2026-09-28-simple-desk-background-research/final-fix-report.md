# Final fix wave — four whole-branch findings

2026-09-30. Base `587d8b4c2feb77513b5100ee1d9f00147dc11686`; implementation commit `4b2759ccc313dc1826b3e5aa12cae6c0419e524c`. Branch `feature/research-round-2`. This report is a subsequent documentation-only commit; its hash is supplied in the handoff.

## Changes and boundaries

1. **Declared indicators:** retained rule evaluation now computes EMA12/26, ADX14, prior-bar Donchian20 channels, and session VWAP with the existing indicator functions/session calendar. RSI/volume-zscore definitions remain unchanged. Input must be finalized, finite, unique/provider-ordered and receipt-ordered, and available no earlier than close. Equal receipt timestamps remain permitted. Missing/warmup/lag operands fail closed even under NOT/OR; crossovers also require the preceding operands. No future fill, new family, threshold, search budget, parameter grid or holdout tuning. Old failed trials/campaign identities are untouched.
2. **Owned collector exit:** the native service publishes only its current owned child's termination. The coordinator invalidates the running generation, stops its monitor, drains owned research/collection concurrently within the existing budget, exposes a recoverable blocked state and permits ordinary Start afterward. Explicit Pause/Quit win over delayed completion. Tests include learning enabled/disabled, early exit, retry, and a genuine subprocess contender exiting while an unrelated lock holder remains alive.
3. **Selected source:** Trade Desk, setup sheet, and both preserved Advanced folder/setup call sites use the coordinator. Stopped explicit selection retains per-source campaign IDs, manifest/runtime hashes, seed and dates; A→B→A restores A's binding. A new source retains the shared registry, but gets no reused campaign. Old preferences decode without the optional bindings array. Inactive bindings receive the same digest/path validation. Failed service selection preserves prior state; failed preference commit attempts restoration of the original service source. Active/draining selection is rejected. Startup uses the saved custom source; opted-in restore waits for that source load. Automatic identity mismatch remains blocking, with no runtime migration.
4. **Notification ownership:** reservation, current-status, outcome, retained-evidence and foreground-admission helpers all use tracked process handles. The static command entry now requires an owner, preventing another unowned call site. Pause/Quit close the launch gate, invalidate permission/evidence/generation tokens, cancel monitoring and drain commands plus pending asynchronous operations within one deadline. Unresolved permission/delivery awaits return interrupted/false at deadline; late completions cannot re-enable notifications, launch status/outcome commands or invent delivery. Interrupted reservations remain uncertain, not delivered or synthetically failed.

No installed app replacement, source/helper packaging, real campaign registration/start/migration, real notification opt-in, OS notification inspection, historical search, frozen-study access, daemon/power changes, push or subagents occurred. Existing prepared587 and installed older binaries are not final-source evidence.

## RED evidence

Commands run from the worktree. Python executable throughout:
`/Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.venv/bin/python`.
Outputs were redirected to the exact files below, followed by a tail for inspection; failing test summaries, not the tail command's exit status, establish RED.

- `python -m pytest tests/unit/test_background_research_data.py -q` → **4 failed, 4 passed**, 2.28s. `/tmp/NowcasterFinalFixIndicatorsRed.log`. The new feature-contract import and real prepared candidates in all three families fail on missing feature columns. First implementation pass also retained a missing-import failure in `/tmp/NowcasterFinalFixIndicatorsGreen.log`; corrected, not hidden.
- `swift test --package-path macos/Nowcaster --filter 'paperSessionRecoversOwnedCollectorExit|paperSessionEarlyExitAndPauseWinOverLateExitDrain'` → recovery test **7 failed assertions**, early-exit/Pause test passed. `/tmp/NowcasterFinalFixCollectorRed.log`. Original coordinator leaves research running and ignores subsequent Start.
- `swift test --package-path macos/Nowcaster --filter shutdownInvalidatesPendingNotificationPermission` → **1 failed assertion**, late permission result re-enables notifications after shutdown. `/tmp/NowcasterFinalFixPermissionRed.log`.
- `swift test --package-path macos/Nowcaster --filter 'failedFolderSelectionPreservesPreviousServiceBinding|shutdownDuringReservationOwnsHelperAndLeavesOutcomeUncertain'` → **2 tests failed, 4 assertions**, 4.484s. `/tmp/NowcasterFinalFixFolderReservationRed.log`. This was a deliberate revert-check: temporarily restored the old pre-validation source clearing and unowned reservation launch, then restored the fixes. Failed selection loses the old binding; reservation PID and pending operation survive shutdown. The test cleans up only its own PID with a matching birth identity. Both tests pass with the fixes.
- `swift test --package-path macos/Nowcaster --filter paperSessionOptedRestoreWaitsForInitialSavedSourceLoad` → **2 failed assertions**, 0.008s. `/tmp/NowcasterFinalFixStartupRaceRed.log`. This newly exposed source-load/restore race was corrected with selection completion waiters; Pause invalidates waiting Start by generation.

The A→B→A and broader new preference cases were added as coverage around the new coordinator API; they are not represented as original-API behavioural RED runs.

## Final focused GREEN

```text
python -m pytest tests/unit/test_background_research_data.py tests/integration/test_background_research_preparation.py tests/integration/test_background_research_training.py -k 'not packaged' -q
26 passed, 1 deselected in 77.66s
/tmp/NowcasterFinalFixPythonFocused.log

swift test --package-path macos/Nowcaster --filter 'paperSession|LivePaper|livePaper|BackgroundResearchServiceTests|TradeDeskPresentation'
61 tests in 3 suites passed in 0.947s (test execution, excluding build)
/tmp/NowcasterFinalFixNativeFinal2.log

/Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.venv/bin/ruff check src/background_research/data.py tests/unit/test_background_research_data.py
All checks passed!

git diff --check
git diff --cached --check
No findings.

python scripts/scan_tracked_secrets.py
Tracked-file and reachable-history secret scan passed
/tmp/NowcasterFinalFixSecretScan.log
```

The Python process and final native process completed with exit 0. The single deselected case is the existing packaged preparation test: its default helper predates these source changes, so running it would be misleading. No full Python/native suite or UI soak was run in this wave, per controller gate sequencing. Intermediate green logs (`IndicatorsGreen2`, `NativeGreen1/2/3`, `NativeFocused`, `NativeFocused2`, `NativeFinal`) remain; `NativeFinal2` supersedes earlier native results.

After implementation commit `4b2759c`, repeated `python scripts/scan_tracked_secrets.py` with this report staged: **Tracked-file and reachable-history secret scan passed**, `/tmp/NowcasterFinalFixSecretScanPostCodeCommit.log`. No source changes followed that verification.

Coverage specifics:

- All declared feature names numerically equal existing `build_indicators`; prefix feature and rule signals are invariant to later appended observations. Real prepared manifests, source registration, scheduler batch, retained payload and candidate evaluator execute every declared indicator in all three families. Existing extreme-future-price, late-revision, training/validation/holdout and immutable-source tests pass.
- Native source tests cover legacy preference decoding, saved custom source, A→B→A identities, failure preservation, active/draining switch refusal, shutdown priority and source-load/restore concurrency.
- Mock permission and delivery continuations hold the boundary deterministically. A real Python reservation helper writes its PID then blocks; shutdown terminates that owned PID, no subsequent command/outcome is launched, and the reservation marker remains. Delivery held across shutdown also produces no late command, no scheduled notification and no fabricated outcome.
- Existing native ownership, authenticated checkpoint resume, tampered-checkpoint/identity, stale/expired-evidence and UI presentation-model checks are included where selected by the filter. These remain source/mock evidence, **not** installed corruption visibility or already-open timed-expiry acceptance.

## Self-review

Reviewed the complete diff, all UI folder/setup call sites, static subprocess calls, late-await mutation guards, per-source persistence and actual evaluator path. Only startup status and tracked-command wrapper call the private static command, both with a required owner. No project-file normalization, packaging, registry transaction/submission code, candidate budgets, promotion gates, costs, protocols or source observations changed. Existing Pause semantics and unrelated-lock-holder tests remain green. Checked that an old learning-disable drain cannot permit source switching or Start before it finishes. Existing model/API callers retain defaults where new collector interface requirements are optional.

Production Git blob hashes at implementation commit:

- `src/background_research/data.py`: `6cb0ad455569ac4e3b3005cff615b1ccbf536d35`
- `LivePaperSignalService.swift`: `3cf3044fa5d299333cf306c50210af9c43198a62`
- `PaperSessionCoordinator.swift`: `c722c07b8e109b19dbf0eb4a03942e48728661fa`
- `PaperSessionPreferences.swift`: `f8a62ffceee17b8930ff2e3d060adaba0e293b8d`

## Remaining gates / honest limits

Scoped rereview and controller-owned final full-source/native, signed build/helper manifest/signature, packaged tests, installed lifecycle/acceptance and remote CI remain. No visual layout/labels changed, only existing controls' ownership and disabled-state wiring; no new screenshots/HIG or accessibility certification is claimed. Existing audit limits (VoiceOver, measured/system contrast, reduced motion/transparency) remain.

Installed corrupted-checkpoint visibility, installed already-open timed expiry, and completed evaluation/checkpoint after installed restore remain unverified. Prior synthetic missing-Donchian failure is preserved as a systematic defect, not reclassified as normal research failure. No profitability/qualified-trade claim. No continuous-feed/24-hour or 150-day capacity claim. The real campaign remains pinned to its prior runtime; any separately recorded replacement/handover needs the controller's explicit preservation ruling and release receipt, not this fix.
