# Native app and background research: release checkpoint

Status: updated app installed; final recovery/shutdown and CI failures under repair.

## 3 October continuation

The September CI run36757730841 completed: native-macos passed; Python had
two failures (1,762 passed, seven skipped). Reduced-core diagnostics reproduced
both. The narrow correction revalidates ownership before publishing evaluated
results. Its recovery test now uses four synthetic attempts within the unchanged
90-second deadline, with stronger completed-result and prefix assertions.
No production research budget or qualification rule changed.

Current focused checks:40 coordinator/training tests,25 forced-low-core
coordinator tests, normal and low-core recovery tests, and eight fixture checks
passed. Refreshed fixtures differ only in source-derived identities. Current
native suite:170 passed,107.205 seconds. Current Xcode build, deep/strict signing,
and exact engine/source/environment verification passed. Full Python release
verification passed: **1,777 tests, zero skips, 1,386.14 seconds**, including
the signed packaged engine and collector. The app is now installed with a
recoverable previous version; installed lifecycle acceptance remains in progress.

Prepared app: `/tmp/NowcasterFinalRelease-20261003/Build/Products/Debug/Nowcaster.app`.
Signed engine SHA256:
`663f0f52c510e22fc343559737f8230f435ebeb70da881e87bce77c8e5a9063d`.
Original prepared native executable SHA256 (before subsequent test builds):
`987fd73d4fb7ab0371202b51609feb250d2465c00a35a1f2b2508c9359f6a202`.
Logs: `/tmp/NowcasterFinalRelease-20261003-{Build,Native,Python}.log`.

The complete default UI run recorded nine passes, nine explicit skips and one
failure; it is not relabeled green. The first clickability failure coincided
with runner assertions and an external WhatsApp window. One unchanged retry
passed all six normal sizes/appearances but failed an immediate large-text
check. The cause remains unproven. The final test-only readiness correction
uses the same ten-second bounds, preserves all assertions, and captures failure
evidence. The final focused test passed all six normal combinations and 200%
text, **54.068 seconds**; scoped review approved it. No production layout fix
is claimed. The final Start action separately passed its default-skipped
preflight; that is not a running-session pass.

Release code: `bef65e45419a578725c6cb17f1aac61c41d23ec9`.
Its [GitHub verification run](https://github.com/james8464/nowcaster/actions/runs/37123233207)
finished with native success and one Python recovery timeout (1,769 passed,
seven skipped). The failing test is
`test_term_checkpoint_and_authenticated_relaunch_keep_batch_and_attempt_prefix`;
an isolated reproduction exposed a test race in which every attempt was already
interrupted and no unreserved work remained. The test-only correction gates the
first durable reservation until real TERM/STOP, preserving three attempts for
normal authenticated recovery. Its normal, reduced-core and coverage checks
each passed two tests. The broader file retained 16 passes, one failure against
an intermediate packaged artifact and one skip; both affected packaged cases
passed individually against the exact signed shipped engine. The intermediate
failure's cause remains unproven. No Python production code or timeout changed;
new remote verification is still required.
Previous run37121939670 passed native and
fixture gates but was superseded before Python completed; it is not a successful
full CI run.

Installed app: `/Applications/Nowcaster.app`. Previous version:
`/Applications/Nowcaster.before-final-20261003-A09BEE4F.app`.
Post-test-build installed native SHA256:
`a0521074f280094261eaa490ab7683af7b214077f2ac31d4469fa86c069919e2`.
Engine source-tree SHA256:
`1c1a1001e7640a87ae32d57c49f3d7d4599b8f7858d698968651417da2cafa10`.
The engine hash remains663f… above; signatures and source/environment binding
were reverified. The original native hash changed during Xcode test-build and
re-signing, and is not presented as the installed identity.

New runtime campaign: `A09BEE4F-F0DE-40B1-B566-EF69008B9B8B`.
The old campaign and complete ledger prefix remain. Manifest preparation changed
only ID/creation time; registration bound the corrected runtime. Only five
active preference pins changed; other settings and opt-ins remained unchanged.
Recoverable backups and the complete comparison receipt are under
`/Users/james/Library/Application Support/NowcasterReleaseHandover/20261003-preflight.Mfnspp`.
Installed real lifecycle acceptance passed: one test, 728.698 seconds. During
ten minutes with the window closed, the same app-owned process set retained
ten BTC and ten ETH observations with zero missing provider minutes. Reopen,
Pause with no new append, Quit and stopped default relaunch passed. Complete
preferences, source prefixes and campaign history remained intact.

This did not establish continuous signal readiness: the provider was classified
stale for approximately 510 of 600 seconds under the unchanged 15-second gate.
The first isolated synthetic run stopped before registration on an unrelated
PaperCreator informational alert. The unchanged retry advanced research with
the window closed but failed the 30-second Quit bound at 30.779632 seconds.
It retained five completed evaluations, eleven rejected attempts and 34 unresolved
reservations, plus its deadline-interruption receipt. A native-only correction
reserves five seconds for final app cleanup; focused RED/GREEN checks, all
172 native tests and scoped review passed. Corrected installed recovery/Quit
and final ordinary Start remain pending. Neither failed run is relabeled green.

The real worker reports Waiting for eligible observations under the unchanged
90/30/30-day schedule, with zero training attempts. It does not consume Codex
credits or require an OpenCode model. This is not a qualified trading service.

## Retained September checkpoint (historical, not current installation state)

The results and pending items below describe their original revision/date.

The simplified macOS interface and app-owned background paper-research worker
are implemented. The final correction fixes missing research indicators,
recovery when the collector exits, retained research-folder selection, and
shutdown ownership for notification helpers. Changes remain on the existing
`feature/research-round-2` branch. No broker orders or credentials are involved.

## Verified on the corrected source

- Implementation `4b2759c`, evidence report `8703e62`.
- Independent scoped review: all four Important findings addressed, no new
  finding in the fix. Earlier review limitations are retained.
- Complete native suite: **170 passed** (32.335 seconds).
- Complete Python suite: **1,771 passed, no skips** (1,008.70 seconds), including
  both release collector checks against the prepared signed collector.
- Affected Python checks: **26 passed**; stale pre-fix packaged test excluded
  from that focused run, not counted as a pass.
- Lint and formatting: clean, 430 Python files checked for formatting.
- Xcode build: succeeded. Prepared bundle passed deep/strict signature and
  exact engine source/manifest checks. This is local development signing,
  not distribution notarization.
- Exact signed engine: **four packaged lifecycle tests passed** (85.85 seconds),
  covering eligible synthetic evaluation, retained checkpoints, output-loss
  shutdown, authenticated cleanup/recovery, and Stop during preparation.

Signed engine SHA256:
`20390caa96b6a900bc54642c07ef18daa233db3592a01faf0caad10a2383cb36`.
Manifest source-tree SHA256:
`d92b58efb27874271abfb6e641023a411a3811bca19dd4cbdc2bff43cde8014c`.

Python/Swift snapshot parity and the tracked-file/reachable-history secret scan
also passed. The complete suite log is `/tmp/NowcasterRelease8703-Python.log`;
native and packaged logs use the same `NowcasterRelease8703` prefix.

## UI verification

After the user unlocked the Mac, the full default UI scheme passed: **10 passed,
8 explicit opt-in skips, 0 failures**, XCTest364.439s. Evidence is retained at
`/tmp/NowcasterRelease8703-UI-Unlocked-20260930.RYHbhB`. This covered the main
routes, settings, keyboard navigation, minimum-size layout and appearance/text
checks. Skipped installed/background scenarios are not counted as passes.
Two geometry warnings and ten main-thread-method warnings remain in the log;
the successful run is not a claim of warning-free rendering or exhaustive HIG
compliance. Earlier locked-host evidence below remains preserved.

The default XCTest scheme could not activate the app while macOS reported
`IOConsoleLocked=Yes` and `CGSSessionScreenIsLocked=Yes`. It was stopped
orderly: exit75, two activation failures, one canceled test, three opt-in skips,
and the remaining tests unrun. None is represented as a UI pass.
A three-second sample of the exact test-owned app showed normal main-thread
run-loop waiting, not a demonstrated application threading hang.

No lock, power, notification or security settings were changed. The failed
result bundle, test logs and stack sample remain retained separately from the
subsequent successful unlocked run.

## CI release failures

CI run36725181861 for0a171de failed. Xcode16.2 rejected inferred actor callback
closures, and the deterministic fixture check found stale source-identity
hashes. Repair0526ddea adds two explicit actor annotations and refreshes the
generated identities. Two clean generations match byte-for-byte, with unchanged
numerical results. The native suite passed170 tests after the annotations;
fixture decoding passed10 tests after resource regeneration, and eight focused
Python fixture/provenance tests passed. Exact supported-compiler CI is still
pending. No failed or pending CI run is represented as successful.

## Installation and research state

The prepared corrected app has **not** replaced `/Applications/Nowcaster.app`.
The installed older app and its real background session are stopped. The old
learning campaign remains immutable and pinned to its earlier runtime. Any
upgrade handover must retain that campaign, its registry records and failed
trials; it must not clear identities merely to make a new runtime start.

The actual desk currently covers BTC/USDT and ETH/USDT Binance spot,
long/stand-aside only. Its fixed 90/30/30-day schedule needs eligible retained
data; starting a worker does not imply it can already train. The prior public
collection test recorded new data but was predominantly stale under the fixed
freshness rule. This is not evidence of continuous usable signals or profits.

Synthetic tests verify software behavior, not an investment advantage. Prior
failed strategies, missing-data gaps and failed evaluations are preserved.
The frozen September 8 study and its paused automation remain untouched.

## September release gates (historical)

At that checkpoint, supported-compiler CI, installed acceptance and explicit
runtime handover remained. See the October continuation above for current results.
Source publication is limited to the existing authorized branch;
check GitHub CI against its exact commit separately from these local results.
No claim
of profitability, safe copy trading, all-HIG compliance, hardware endurance or
complete accessibility certification is made.

All retained implementation decisions and their risks are in
[Release decisions](2026-09-30-release-decisions.md).
