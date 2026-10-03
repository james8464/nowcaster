# Large text UI readiness diagnosis

The final focused appearance matrix passed on 3 October 2026: six normal appearance and size cells plus minimum-window 200% text, 1 test, 0 failures, exit 0, 54.068 seconds. This verifies the final test source. The earlier intermittent failures remain recorded; their root cause is unproven. No production defect or production repair is claimed.

## Evidence and limits

- Original full UI run: `/tmp/NowcasterFinalRelease-20261003-UI.log` and `.xcresult`; 9 passed, 9 skipped, 1 failed at original line 169, the normal-cell controls readiness deadline. Its log included another app covering Nowcaster; that does not independently establish the failed assertion's cause.
- Unchanged focused retry: `/tmp/NowcasterFinalRelease-20261003-MatrixRetry.log` and `.xcresult`; all six normal cells passed, then original line 185 immediately checked action hittability after seven text-size shortcuts and failed. No failure-state screenshot, action frame, enabled state, or AX tree was captured. Its 12 captures precede large text.
- Diagnostic-only focused run: `/tmp/NowcasterFinalRelease-20261003-LargeTextDiagnostic.log` and `.xcresult`; passed, 59.659 seconds. Pre-assertion diagnostic queries added time, so this pass cannot prove the cause or a repair.
- Final test-only focused run: `/tmp/NowcasterFinalRelease-20261003-MatrixFinal.log` and `.xcresult`; passed, 54.068 seconds. Both readiness results completed before diagnostic collection. The original action assertion, seven shortcuts, Settings value `200%`, and Quit assertion passed.

Both diagnostic checkpoints showed app state 4 (running in foreground), window `(36, 354, 820, 620)`, action frame `(762.5, 446, 73.5, 24)`, and action exists/enabled/hittable all true, label `Start`. AX status value was `Not started`. Screen inspection confirmed the Start button fully visible in foreground Nowcaster. Another app behind Nowcaster is not evidence of occlusion. AX container `Disabled` annotations do not establish a disabled Start button: its explicit enabled query was true.

These observations exclude persistent Start clipping or a disabled action at the captured checkpoints. They cannot distinguish transient startup, foreground interference, or layout settling at the earlier uncaptured failure.

## Scoped test hardening

Only `macos/Nowcaster/UITests/NowcasterUITests.swift` changed: the large-text phase explicitly activates the app, awaits actual 820 by 620 geometry, performs its seven shortcuts, and awaits action hittability. Both waits use the existing normal-phase 10-second bound. Geometry and control readiness results are stored before diagnostics and asserted afterward, so diagnostic latency cannot rescue a missed deadline. Full AX, frame/state text, and screen attachments are retained on readiness success or failure. The original hittability and 200% assertions remain.

This conforms the large-text phase to existing test preconditions; it is justified as test readiness hardening, not a proven application fix. Scoped diff review found no production changes, weakened assertions, increased existing timeout values, or other-app manipulation. `git diff --check` passed. Existing headermap/AppIntents build warnings and a main-thread runtime warning remain; no warning is established as the failure cause.

Attachments are exported under `/tmp/NowcasterFinalRelease-20261003-MatrixFinal-attachments` (28 files plus manifest). The final state, AX, and screen files are respectively `4F0D8D48-62F9-49D2-B295-3F3F2DEAA4C4.txt`, `9DD81470-60D1-46CC-BFF1-04B5CD712B80.txt`, and `B1DD66AF-70A8-4D29-BFBB-A1BA59CAE722.png`. Screens include incidental desktop content; keep them local.

No full UI, Python, or native CI repeat was performed here. No production edit, installation, preference/registry mutation, commit, or push was performed.
