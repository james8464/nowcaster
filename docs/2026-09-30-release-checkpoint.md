# Native app and background research: release checkpoint

Status: source-verified checkpoint, not a completed installed release.

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
verification is in progress; installation and new CI remain pending.

Prepared app: `/tmp/NowcasterFinalRelease-20261003/Build/Products/Debug/Nowcaster.app`.
Signed engine SHA256:
`663f0f52c510e22fc343559737f8230f435ebeb70da881e87bce77c8e5a9063d`.
Native executable SHA256:
`987fd73d4fb7ab0371202b51609feb250d2465c00a35a1f2b2508c9359f6a202`.
Logs: `/tmp/NowcasterFinalRelease-20261003-{Build,Native,Python}.log`.

The explicitly opted-in final Start interaction passed its default-skipped
runner preflight (one skip, zero failures). That is not a real-session pass.
The retained September results below describe their original revision, not
the newly prepared runtime.

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

## Remaining release gates

Supported-compiler CI, installed acceptance and explicit runtime handover
remain. Source publication is limited to the existing authorized branch;
check GitHub CI against its exact commit separately from these local results.
No claim
of profitability, safe copy trading, all-HIG compliance, hardware endurance or
complete accessibility certification is made.

All retained implementation decisions and their risks are in
[Release decisions](2026-09-30-release-decisions.md).
