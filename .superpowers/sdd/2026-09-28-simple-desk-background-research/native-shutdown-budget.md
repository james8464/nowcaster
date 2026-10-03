# Native shutdown cleanup headroom

Native callers now give owned child drains 25 seconds, reserving five seconds within the unchanged 30-second end-to-end Quit contract. Focused regression tests failed against the original 30-second calls, then passed; the full serial native suite passed 172 tests in 5 suites, 51.081 seconds, exit 0. Corrected installed Quit and authenticated recovery acceptance remain root-owned and unverified here.

## Cause and retained failure

The unchanged installed retry failed `BackgroundSessionUITests.swift:281`: Quit took 30.779631972312927 seconds against the 30-second assertion. Evidence: `/tmp/NowcasterFinalSyntheticRetry-20261003-A09BEE4F.log` and matching `.xcresult`. The controller's process observations and durable interruption receipt established that research used its full 30-second grace before deadline fallback; app and helpers subsequently exited. The failed run, completed work, and 34 unresolved reservations remain retained.

Source tracing found that `AppModel.shutdownForApplicationTermination()` drains paper sessions, legacy monitoring, and legacy jobs concurrently. Both the paper-session workers and legacy `EngineRunner` could consume 30 seconds before application cleanup. Reducing only research would leave the legacy path capable of the same overrun. Legacy monitoring already uses four seconds of graceful wait plus two seconds after termination, so it needs no change.

## Changes and verification

- `Services/PaperSessionCoordinator.swift`: shared `NativeShutdownBudget.ownedDrain = 25 seconds`; propagated through nine worker shutdown calls covering Quit, Pause, collector exit, learning disable, retry, and late startup cleanup. Earlier stop operations therefore cannot retain the old 30-second grace while Quit begins.
- `AppModel.swift`: legacy job draining uses the same 25-second budget and remains concurrent with paper/monitor shutdown.
- `PaperSessionCoordinatorTests.swift`: captures boundary budgets in existing doubles; verifies concurrency and repeated-Quit idempotence, prior learning drain overlapping newer Pause, late startup cleanup, and four parameterized stop routes.
- `AppModelTests.swift`: verifies the legacy budget and paper-session completion while the legacy drain is still pending. No test-only production API was introduced.

RED: `swift test --no-parallel --filter 'paperSessionQuitDrainsCollectionAndResearchConcurrentlyWithinOneBudget|paperSessionStopPathsReserveTimeForApplicationCleanup|paperSessionDisableCompletionCannotOverwriteNewerPause|paperSessionRapidPauseRejectsLateStartPublication|applicationQuitReservesCleanupTimeForLegacyJobsAndDrainsPaperConcurrently'` failed 5 tests with 10 expectation issues, each observing the old 30-second budget. Log: `/tmp/NowcasterShutdownBudget-20261003-RED.log`.

GREEN: the same focused command passed 5 tests, including four stop-route cases, with no issues, exit 0. Log: `/tmp/NowcasterShutdownBudget-20261003-GREEN.log`.

Full suite: `swift test --no-parallel` passed 172 tests in 5 suites, no issues, exit 0. Log: `/tmp/NowcasterShutdownBudget-20261003-NativeFull.log`. Existing real-process tests cover ownership, unrelated-child survival, lower-level deadline fallback, interruption receipts, and late-launch rejection. `git diff --check` passed. Production and test source was frozen after focused GREEN.

## Scope and risk

Lower-level 30-second caps, authenticated ownership checks, durable STOP/checkpoint handling, interruption receipts, and signaling protections are unchanged. UI deadlines/assertions and Python/runtime/research rules are unchanged. No packaged app rebuild, installation, UI run, commit, or push was performed by this agent. Other agents own unrelated documentation and UI acceptance edits.

Five seconds of cleanup headroom is a bounded scheduling allowance, not proof of the final installed Quit duration. Less graceful time can interrupt more work between 25 and 30 seconds. Installed acceptance must confirm the unchanged outer deadline, retained checkpoints, authenticated resume, and unrelated-process survival. Native boundary/concurrency tests establish budget propagation; they do not replace that end-to-end acceptance.
