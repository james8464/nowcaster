# Nowcaster installed-app user-flow check — 27 September 2026

This is a chronological record. The initial blocked inspection below is retained;
the XCTest continuation at the end records the later tests and fixes.

## Verdict

Incomplete. The installed app opened and its Live Monitor navigation worked, but the current starting state was a month-old demo snapshot with monitoring stopped. This run did not verify the complete day-trading assistant workflow. Do not treat it as an all-markets, trading-lifecycle, or profitability pass.

Target: `/Applications/Nowcaster.app`. User goal: a usable assistant that observes supported markets, explains its strategy and market context, presents a conditional long/short setup with entry, invalidation/stop and targets, and follows the setup through management and exit.

## Steps and observed health

1. **Open installed app — passed, narrowly.** Connected to its normal macOS `Today` window and read its sidebar and menu bar. Nowcaster remained running throughout this check. Dock behavior, fresh-install behavior and relaunch persistence were not tested in this run.
2. **Read the initial briefing — renders, but unsuitable as a current-market briefing.** The toolbar displayed `Demo snapshot` and `1 mth, 1 day`. The screen showed 5 instruments, 2,000 research signals, 2,000 forecasts and 0 quality issues. Visible rows repeated SBUX short research and COST long research, with `seasonal_naive · fundamentals_only · expectation_proxy` and `Not calibrated`. This is the observed demo presentation, not evidence of present-day entries or of an intraday trend strategy. Screenshot: [01-today.png](01-today.png).
3. **Navigate to Live Monitor — navigation passed; collection not tested.** Clicking the sidebar opened `Live Monitor`. Its accessible UI reported `Stopped`, `2 stocks · 2 crypto`, `Confirmed 5-minute decisions · 1-minute risk monitoring`, `Start Monitoring`, and `No Live Events`. Its copy explained that it needed finalized bars and that monitoring stops when the Mac sleeps, goes offline or quits. These are UI claims; provider behavior and bar freshness were not verified in this run.
4. **Inspect live strategies and trade management — blocked.** A Live Monitor screenshot was retained and subsequently visually confirmed: [02-live-monitor-stopped.png](02-live-monitor-stopped.png). Further navigation encountered repeated `Sky Computer Use native pipe closed before response` errors. Resetting the controller, refreshing its inventory and reconnecting to the exact installed app path did not recover control. Strategy Lab, setup detail, entry/stop/target fields, position management, exits and notifications were therefore not exercised.

![Observed initial Today screen](01-today.png)

## Findings tied to the observed steps

- **High impact, step 2:** the first view presents historical earnings research rather than the current-market situation the user is looking for. The date/mode badge provides useful disclosure, but a user should not need to infer why the opening screen is unrelated to today's trading workflow.
- **High impact, step 3:** the visible monitor is stopped, so this app session is not currently showing live-monitor events. The separate prospective study was not inspected or changed; its collector status cannot be inferred from this screen.
- **Medium impact, step 2:** repeated rows do not show dates, horizons or another visible distinction. A user cannot tell why two SBUX rows or four COST rows differ without investigating further.
- **Medium impact, step 2:** internal labels such as `seasonal_naive` and `expectation_proxy` do not explain a trading rationale in beginner-friendly language. The `Not calibrated` disclosure is useful, but the screen does not provide a current trigger or management plan.
- **Accessibility scope:** sidebar rows, the selected page, monitor status and Start Monitoring control were exposed to accessibility inspection. Keyboard navigation, VoiceOver, focus transitions, contrast and complete accessibility compliance were not tested.

## Control-tool failure evidence

The installed Nowcaster process remained alive (PID 77059 at inspection). macOS produced crash reports for `SkyComputerUseService`, not Nowcaster:

- `/Users/james/Library/Logs/DiagnosticReports/SkyComputerUseService-2026-09-27-131739.ips`
- `/Users/james/Library/Logs/DiagnosticReports/SkyComputerUseService-2026-09-27-131750.ips`

The latter reports `EXC_BREAKPOINT` / `SIGTRAP`, with `Array.remove(at:)` and repeated `Sequence.compactMap` frames. This supports a failure in the UI-control service. It does not establish a Nowcaster crash or a cause inside Nowcaster.

## Required continuation

After native control is restored, resume hands-on testing from Live Monitor. Verify current public data, supported/unsupported asset handling, strategy rationale, timestamps, entry conditions, stop/target levels, expiry, hypothetical position management, exits, error states and recovery. Use an isolated paper/replay session where a live setup is unavailable; clearly label replay evidence. Do not alter the frozen prospective study, use broker credentials, enable qualified alerts or place orders. Do not use prior automated tests as substitutes for the missing hands-on checks.

No app source, trading rules, saved credentials, alerts, study state or market positions were changed during the initial inspection above. The app was left on Live Monitor, stopped as found.

## Continuation: native desk and Xcode diagnosis

The implementation continuation added a native Trade Desk opening page, non-destructive setup, calendar import, and three separately identified one-minute research variants: EMA/ADX trend, Donchian breakout and VWAP trend continuation. Starter coverage is BTCUSDT and ETHUSDT public spot data, long/stand-aside only; not stocks, oil or executable short selling. Existing manifests, retained results and the frozen prospective study were not changed.

The prior starter registration referred to an unavailable EMA identifier/version/interval combination. The new registration resolves real configured generators. Independent review also caught a mismatch where the displayed context could come from the last candidate rather than the first eligible candidate, and a disabled starter definition could break unrelated registry loading. Both were reproduced in failing tests and corrected.

Xcode was observed running a bare `NowcasterApp` executable from the Swift package. The actual `Nowcaster.xcodeproj` was opened and the identified `Nowcaster.app` built and launched. Its bundle identifier is `com.james8464.nowcaster`; strict/deep signature verification passed. The missing-bundle-identifier message did not recur in the captured real-app launch.

Apple `linkd.autoShortcut` connection errors (4097) persisted in that ad-hoc build. A subsequent read-only system-log check found the service explicitly rejecting Nowcaster with `requiresValidatedBundle`. An isolated copy of the same bundle was signed with the already-installed Apple Development identity and launched through native controls. At 21:02:11 UTC, the service logged `Accepting [3756]:com.james8464.nowcaster`; no corresponding 4097 appeared for that launch. This controlled signing change addresses the observed rejection, not every possible cause of code 4097.

Xcode now supports an ignored per-machine signing configuration, with the installed development team configured locally. Both Debug and Release load it; the portable repository fallback remains ad-hoc for machines without a certificate. Embedded helpers inherit the resolved signing identity. No private keys or certificates were exported or stored in Git, no system service was disabled, and logging was not suppressed.

Hands-on verification remains incomplete. The native control service failed again when inspecting the new app. Xcode's alternative view-hierarchy capture timed out; a later debug stop showed a CoreFoundation/SkyLight notification-lock trap. A direct Trade Desk launch without the debugger also did not restore UI control. Temporary launch arguments and debugger changes were removed. These observations do not establish that all screens work, or that the operating-system warning is harmless.

### Actual packaged runtime check

The embedded helper from the newly built app, not a source interpreter, created an isolated retained directory:

`/Users/james/Library/Application Support/Nowcaster/PaperResearch/verification-20260927-native-desk`

Protocol hash: `9027e6b08675b96a517a493f4ab1a846acd130d4bbb4702f4bcf18d50dca9d24`.

- Setup and one collection pass succeeded at 20:47 UTC on 27 September.
- A bounded start/stop run from approximately 20:55:25 to 20:59:45 UTC collected eight additional observations, ten total retained. Latest BTC and ETH bars closed at 20:59:00 UTC and were received at 20:59:06.133997 and 20:59:09.137276 UTC respectively; both provider-error fields were null.
- Stop was verified at 20:59:45.603294 UTC with `kind=stopped` and `user_stopped`. The interruption and all records remain retained.
- The engine abstained while calendar/evaluation evidence and continuity warmup were missing. It also reported stale observations between eligible receipts; this brief check is not proof of continuous service reliability.
- No qualified suggestion, order, completed paper position, or notification was produced. No account credentials were used.

### Verification boundaries

Expanded focused Python checks: 173 passed. Native checks: 126 Swift Testing tests and 4 XCTest tests passed. Full Python suite checkpoint: 1,624 passed, two built-helper tests skipped. The two subsequent signing configuration tests also passed. Prefix invariance was tested against future-suffix changes for all three real starter generators. These results establish the tested contracts, not historical profitability or completed forward qualification.

The built-helper checks were then explicitly enabled. The registered-state check passed, but the local TLS proxy test failed with an untrusted test certificate: production now explicitly loads certifi and does not accept `SSL_CERT_FILE` as an injected root. The fixture was corrected to add its CA only to a temporary copied helper bundle and re-sign that copy; hostname and certificate validation remain enabled. The original installed bundle and its trust store are unchanged. The status test exercises the unmodified signed helper; the local proxy evaluation test exercises the controlled copy, not a claim about production market prices. Final combined bundle, TLS and packaging checks: **14 passed**, including both previously skipped cases.

Final Xcode build succeeded with local development signing; both app and paper helper have the expected team identity and strict/deep signature verification passed. The installed app was updated at `/Applications/Nowcaster.app`, with its previous version retained at `~/Library/Application Support/Nowcaster/AppBackups/Nowcaster-before-trade-desk-20260927.app`. Temporary app instances and the old installed instance were normally quit through Activity Monitor; only the updated installed app was reopened. macOS logged acceptance of PID 13606 at 21:10:54 UTC without the reported 4097 error for that launch. Native-control inspection still failed with only this app instance running.

Still required: complete native user interaction, calendar import and invalid-input recovery through UI, qualified replay entry/management/exit, notification delivery, persistence after relaunch, and longer-duration feed/error recovery. None is silently counted as passing.

## User-authorized XCTest continuation

The user explicitly authorized Apple's XCTest UI runner after the native-control
service continued crashing. Added a real `NowcasterUITests` target to the existing
shared Nowcaster scheme. It operates the actual bundled app, native file sheets
and normal Quit/relaunch. It does not inject live prices or bypass eligibility.

### Issues reproduced and corrected

- Two competing SwiftUI file importers prevented **Choose Research Folder** from
  presenting its sheet. One presenter now routes folder/calendar selections.
  The failing UI runs are retained, not discarded.
- A healthy collector could retain prices while showing **Awaiting evidence**:
  provider health was only written after strategy evaluation, which is suppressed
  during reconnect warmup. A separate, protocol-bound health projection now updates
  before that gate. No strategy evaluation, publication or qualification is enabled
  by this projection. Its timestamps still expire; malformed or mismatched health
  is rejected, not silently replaced with older evidence.
- An old UI assertion queried SwiftUI's Markets table as an accessibility Table;
  the observed macOS hierarchy exposes an Outline with the correct identifier.
- The synthetic lifecycle fixture reused a candidate identity for both symbols.
  The native decoder correctly rejected duplicate decisions. The fixture now
  derives per-asset identities, with a separate retained fixture directory. No
  decoder or safety check was relaxed.

### Evidence boundaries

The live desk is `~/Library/Application Support/Nowcaster/PaperResearch/paper-desk-v1`,
protocol `4c902a65e01dd99f20c1a4287952388422d9389f9cb4bea455b60d37f852efcc`.
All observations from successful and failed attempts remain. An earlier failed
test left its paper collector running; it was stopped using its normal control at
21:58:31 UTC. Test teardown now attempts Stop and normal Quit even after an assertion
failure. The frozen prospective study was neither started nor altered.

Calendar rejection uses a source-attributed, deliberately limited BLS JOLTS file.
Its September 29 event window does not cover September 27, so rejection is the
correct outcome. There is still no complete automatic calendar provider. Valid
calendar import is exercised only in an explicitly marked synthetic directory.
No fabricated no-event calendar is imported into live research.

Synthetic fixture: `~/Library/Application Support/Nowcaster/UIAcceptanceFixtures/replay-20260927-2201/test_later_retained_bars_advan0`.
The integration test demonstrates causal origin → management revision → target
completion → idempotent reload. The native UI test checks its historical display
and rejection as a current entry. This is not a historical strategy-return study,
nor proof of an executable fill or a profitable live trade.

A single evaluation of the earlier ten-observation real-data verification desk
returned **insufficient_data / incomplete_fold for all six candidates**, with no
completed validation fold or simulated trades. All results were retained. The
90-day training, 30-day validation and 30-day sealed window, trade-count and data
coverage requirements remain unchanged. Future observations cannot be manufactured
by completing UI work.

### Review and final verification

Independent review requested stronger fresh-receipt assertions, unconditional
cleanup, actual elapsed-duration checks and unique synthetic import filenames.
All four were addressed. Live acceptance now requires new timely receipts for both
assets after Start; the soak requires elapsed monotonic time and recent receipts,
not just an existing row count. Raw `.xcresult` recordings stay local because they
can include unrelated desktop content.

The ten-minute UI collection/control test passed (644.995 seconds including UI
overhead). Its collector ran 22:01:46–22:12:13 UTC, retaining ten new BTC and ten
new ETH observations. Receipt latencies were 5.14–13.14 seconds. The 22:10 minute
was absent after an `invalid_observation` event at 22:10:15; this is retained
coverage loss, not uninterrupted-feed proof. All 54 observations then present
had distinct source keys. Stop/reopen preserved the protocol and prior bytes.

The first full UI run had five passing scenarios and one runner-side fixture
failure: XCTest could read but could not write the synthetic calendar directly
into the app's Application Support directory. The test now writes within its own
container and retains the exact JSON as an attachment before importing it through
the app. No app permission or eligibility check was weakened.

Release verification also reproduced an incremental Xcode signing failure:
the runtime script changed `engine-manifest.json` without declared outputs, so
Xcode could skip re-signing the outer app. The build phase now declares its
generated resources and always runs so Python changes remain included.

Full Python checkpoint: **1,627 passed, 2 skipped** (1,082.19 seconds). The two
release-helper cases were subsequently covered by the **14-passing** packaged
helper/TLS/packaging run. The later incremental-signing contract has **8 passing**
packaging checks. Native checkpoint: **128 Swift Testing + 4 XCTest passed**.

### Final acceptance checkpoint

The subsequent live acceptance test passed in **192.451 seconds**, including
new timely BTC/ETH receipts, rejected calendars, invalid-folder recovery,
Stop and ordinary Quit/relaunch. The installed-app quit-before-upgrade check
also passed. Synthetic calendar import passed, but the history assertion initially
failed because a center click selected the disclosure label instead of expanding
its arrow. The test now targets the observed leading arrow and checks expansion.

Two attempts to verify that last adjustment stopped **before any test began**:
`nowcaster-ui-history-20260927.xcresult` and
`nowcaster-ui-history-retry-20260927.xcresult`, retained under `/tmp`.
Both report `Timed out while enabling automation mode`. A read-only sample of
`testmanagerd` shows `XAMLocalAuthenticationProvider authorizationWithError`
waiting in LocalAuthentication. No authentication, security or power setting was
bypassed. Local authentication was requested from the user. The latest history
interaction and replacement installed-app UI verification remain unverified.

A repeated packaging build also exposed a missing file in PyInstaller's global
cache. Both packagers now use this checkout's own build cache. The added regression
contract brings the focused packaging checks to **9 passed**. The next actual
incremental build passed, including strict/deep signature verification; one further
unchanged-app-source rebuild was requested to verify the signing regression.

The successful and failed UI runs, existing losses and collection gaps are all
retained. No actual current qualified entry/management or macOS notification
banner was observed. Completed synthetic lifecycle calculations and native model
checks are not substitutes for those missing UI/live results. The default desk
still needs verified current calendar coverage and enough future retained data
for its frozen qualification protocol. It is not copy-trading-ready.

Both isolated-cache incremental builds finished successfully and passed strict/deep
signature verification, including the repeat with unchanged app source. The final
bundle was installed at `/Applications/Nowcaster.app`; its previous version is
retained at `~/Library/Application Support/Nowcaster/AppBackups/Nowcaster-before-xctest-fixes-20260927-2234.app`.
The installed signature and source/executable manifest were verified again.
Native-control inspection of the replacement still closed its pipe without a
response. This does not close the remaining installed-window acceptance item.
The installed app did launch as PID 64069. At 22:33:19 UTC, macOS `linkd` accepted
its identified app connection without the reported 4097 error in that launch's
filtered log. Collection remained stopped; no alerts were enabled.

Final checks against the **installed** helper plus TLS and packaging contracts:
**16 passed in 34.42 seconds**. The original installed bundle still passed
strict/deep signature verification afterward. Ruff passed with all 408 Python
files formatted; tracked-file and reachable-history secret scans passed. No local
signing identity/configuration, raw desktop recordings or study files were staged.

Inspected window-only captures (no unrelated desktop content):

- [Successful synthetic calendar import](03-synthetic-calendar-import.png): test
  fixture only, stopped, old context unavailable; not live trading evidence.
- [Stale data means stand aside](04-stale-stand-aside.png): captured during the
  live test before a later display refresh, not evidence of a fresh entry.

### Fresh-install CI compatibility

Delivery `2abdfd3` triggered CI 36355836468, which **failed** in the deterministic
research fixture and live-monitor checks. Read-only inspection of the preceding
CI run 36350891811 also found a formatting failure (corrected in this delivery)
and the same database reflection error. Fresh CI resolved SQLAlchemy **2.1.1**,
whereas the tested installed app uses **2.0.52**, with DuckDB 1.5.5 and
duckdb-engine 0.17.0 in both cases.

A separate fresh environment at `/tmp/nowcaster-dependency-check.sOpHxi/venv`
reproduced the migration failure with 2.1.1 (`pg_catalog.pg_collation` binding).
The upstream dialect documents its inheritance of PostgreSQL behavior; see
[duckdb-engine's compatibility notes](https://github.com/Mause/duckdb_engine#things-to-keep-in-mind).
This release now pins the locally tested **2.0.52**, rather than patching database
ledgers or changing reflection rules. The dependency contract failed before the
constraint and passed afterward. The fresh environment's repository, migration,
startup and packaging checks then passed: **24 tests**. Neither the primary
installed environment nor the frozen study environment was upgraded or modified.

The affected live-monitor target passed in the fresh environment: **172 Python
tests, 16 native checks and the deterministic replay**. The regenerated CI snapshot
had only 180 source-derived identity differences (36 each of strategy cohort,
ensemble cohort, evidence cohort, contextual protocol and outcome-index hashes).
No market values, trade results, strategy weights or safety decisions differed.
Updated the generated CI artifacts and the native fixture using the existing
synchronization tool; Python/native research semantic parity passed. Old fixtures
remain in Git history; these generated demo artifacts are not study records.

After the fixture refresh, **128 Swift Testing + 4 XCTest tests passed again**.
The full engine rebuild and subsequent native-resource rebuild both succeeded.
The final installed bundle passed strict/deep signing and source/executable
manifest verification. Its immediately previous version is retained at
`~/Library/Application Support/Nowcaster/AppBackups/Nowcaster-before-dependency-pin-20260927-2245.app`.
The app was normally quit before replacement and collection remains stopped.
Fresh-environment fixture parity, deterministic reproducibility, failure isolation
and bounded historical-replay contract tests also completed: **10 passed in
124.85 seconds**. Packaging dependency checks: **10 passed**. Lint and formatting
passed again. Remote CI for the compatibility-fix commit remains a separate check;
the previously failed run is retained, not represented as passing.
