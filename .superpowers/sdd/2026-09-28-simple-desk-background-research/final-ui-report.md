# Final default UI scheme — host-blocked, incomplete

2026-09-30, source revision `8703e62a11dc32adce694b6fc4c475ddffa6370c`. **Not passed.** Controller requested cancellation after independently observing `IOConsoleLocked=Yes` and `CGSSessionScreenIsLocked=Yes`. No retry, timeout change, foreground workaround, permission bypass or source/test edit.

## Command and preserved evidence

Worktree: `/Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.worktrees/research-round-2`.

```sh
NOWCASTER_SKIP_ENGINE_BUNDLE=1 xcodebuild test \
  -project macos/Nowcaster/Nowcaster.xcodeproj -scheme Nowcaster \
  -destination 'platform=macOS,arch=arm64' \
  -derivedDataPath /tmp/NowcasterRelease8703-20260930.kmY6fX \
  -resultBundlePath /tmp/NowcasterRelease8703-UI-20260930.RwaRHh/FullScheme.xcresult
```

No `NOWCASTER_UI_*`/`TEST_RUNNER_NOWCASTER_UI_*` opt-ins were inherited. Existing default tests generated marked isolated roots. Prepared app used the controller's DerivedData; no helper rebuild, installed replacement or real-study/campaign action. Full Python verification ran separately under the controller; no evidence attributes these activation failures to Python concurrency.

Evidence directory `/tmp/NowcasterRelease8703-UI-20260930.RwaRHh` contains:

- `test.log`: complete build/test/cancellation output.
- `FullScheme.xcresult`: retained native result bundle.
- `summary.json`, `tests.json`: `xcrun xcresulttool get test-results summary|tests --path … --compact` exports.
- `Nowcaster-31531-activation.sample.txt`, `sample-command.log`: bounded read-only stack sample of the exact test-owned app.

## Actual outcome

Owned xcodebuild PID31201 started at **15:37:38 CEST**; XCTest suite began **15:37:45.601**. Controller-directed `SIGINT` was sent only after rechecking that PID's exact xcodebuild command/path. Terminal result at **15:40:41.344**: `TEST INTERRUPTED`, **exit75**, testing elapsed **176.433s**. Result bundle records six tests: **0 passed, 3 failed, 3 skipped**. The third failure is cancellation, not a completed assertion failure:

| Test | Actual result |
|---|---|
| `NowcasterUITests/testDeskAppearanceMatrixAndLargeText` | Failed at `app.launch()`, source line156, unable to activate; state `Running Background`. **62.469s**. |
| `NowcasterUITests/testKeyboardSidebarNavigationAndSettingsShortcut` | Same activation failure at line203. **62.121s**. |
| `NowcasterUITests/testLargeTextCanReachLowerDeskHelp` | Started15:39:50.564; canceled during launch by controller's host-lock ruling. Result text `Testing was canceled`. |
| `BackgroundSessionUITests/testOptInInstalledNarrowAppearanceAndKeyboardEvidence` | Skipped, opt-in absent, **0.156s**. |
| `BackgroundSessionUITests/testOptInInstalledRealTenMinuteBackgroundLifecycle` | Skipped, opt-in absent, **0.128s**. |
| `BackgroundSessionUITests/testOptInSyntheticTrainingCheckpointAndAuthenticatedResumeWithClosedWindow` | Skipped, opt-in absent, **0.087s**. |

Remaining default and opt-in tests did not execute before cancellation. They are neither passes nor observed skips in this run. No appearance/layout/keyboard conclusions can be drawn from tests that could not activate the app.

## Bounded diagnostic and cleanup

First sampling attempt for the previously observed test-owned PID31359 found it had already exited; no sample was obtained for that PID. Then `sample 31531 3 -file …/Nowcaster-31531-activation.sample.txt` succeeded at **15:40:12.124 CEST**. Its executable was the prepared test app, launched15:39:51.776 with `--destination=tradeDesk --ui-minimum --ui-dark`. The main thread had **2617/2626** sampled frames waiting in normal CFRunLoop/mach-message processing, plus brief UI layout/update work. This is not evidence of a main-thread application hang. Combined with the controller's independently observed locked console, the current blocker is unavailable foreground interaction.

After orderly xcodebuild cancellation, read-only process checks found PID31201 (xcodebuild), PID31269 (test runner) and PID31531 (test app) absent. No prepared test app/runner or `nowcaster-engine … background-research` / `nowcaster-paper-signals … start` process remained in the scoped process check. No unrelated process was signaled, and no real installed app action was taken.

UI verification remains open. Restart requires explicit confirmation that the host is unlocked and the controller's next instruction. No installed acceptance, fresh HIG/VoiceOver/system-mode claim, full-source test result, signed-package result or release approval is implied by this report.
