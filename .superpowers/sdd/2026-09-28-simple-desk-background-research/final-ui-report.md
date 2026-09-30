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

## Unlocked full-scheme continuation — passed

User explicitly confirmed unlocking; controller verified `IOConsoleLocked=No`. On source-equivalent documentation HEAD `0a171de0f338bf5466a7fe1404ff218d42e47df5`, ran the **same complete default scheme**, same prepared DerivedData, same skip-helper flag, no inherited UI opt-ins, with fresh result path:

```sh
NOWCASTER_SKIP_ENGINE_BUNDLE=1 xcodebuild test \
  -project macos/Nowcaster/Nowcaster.xcodeproj -scheme Nowcaster \
  -destination 'platform=macOS,arch=arm64' \
  -derivedDataPath /tmp/NowcasterRelease8703-20260930.kmY6fX \
  -resultBundlePath /tmp/NowcasterRelease8703-UI-Unlocked-20260930.RYHbhB/FullScheme.xcresult
```

**Exit0, TEST SUCCEEDED: 18 tests, 10 passed, 8 explicitly skipped, 0 failures.** XCTest duration364.439s; xcodebuild testing duration378.067s, terminal19:50:38.390 CEST. All ten default tests passed in this single run; no retry or threshold change. Console remained unlocked during periodic read-only checks. Previous locked failures above remain retained and are not erased/reclassified as passes.

Passed tests (seconds): appearance matrix47.828; keyboard11.260; large-text help10.772; minimum Backtests10.958; monitor identifiers4.824; independent settings/menu12.737; all12 primary/advanced destinations232.448; simple routes/setup15.053; wide detail10.426; no-autostart Trade Desk6.339.

Eight skips were the three BackgroundSession installed visual/real/synthetic tests listed above, plus TradeDesk explicit live setup/import, installed normal Quit, installed bundle open, sustained public collection, and synthetic completed lifecycle. Their opt-ins were intentionally absent; **no installed/background acceptance is inferred**.

Evidence directory `/tmp/NowcasterRelease8703-UI-Unlocked-20260930.RYHbhB` retains `test.log`, `FullScheme.xcresult`, `summary.json`, `tests.json`, `Nowcaster-57935-navigation.sample.txt`, and `navigation-sample-command.log`. The result summary reports **two negative-width geometry runtime warnings and ten main-thread-method warnings**; these remain concerns, not test failures hidden by the pass result. No new visual/HIG/accessibility certification is claimed.

The route test was a latency outlier. At one scoped process sample the owned app used74.8% CPU. A three-second read-only stack sample at19:49:49.704 CEST found788/1222 main-thread samples in normal run-loop waiting and390/1222 servicing XCTest accessibility hierarchy requests; this is evidence of accessibility snapshot work during that interval, not a general hang or a proven production-performance cause. Actual timing was preserved without inflating limits.

Terminal read-only check found owned xcodebuild57566, runner57652, navigation app57935 and final app58682 absent. No source/test changes, helper rebuild, install, real campaign action, notification permission, power change or frozen-study access during that UI run. This report was committed with the subsequent narrow CI compatibility/fixture repair in 0526ddea7435877156993a4053107da4224b0166. Remote CI failures are separate release blockers; this UI pass does not clear them.
