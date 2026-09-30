# Native app and background research: release checkpoint

Status: source-verified checkpoint, not a completed installed release.

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

## UI verification is blocked by the locked Mac

The default XCTest scheme could not activate the app while macOS reported
`IOConsoleLocked=Yes` and `CGSSessionScreenIsLocked=Yes`. It was stopped
orderly: exit75, two activation failures, one canceled test, three opt-in skips,
and the remaining tests unrun. None is represented as a UI pass.
A three-second sample of the exact test-owned app showed normal main-thread
run-loop waiting, not a demonstrated application threading hang.

The user has been asked to unlock the desktop. No lock, power, notification or
security settings were changed. The failed result bundle, test logs and stack
sample remain retained. A fresh UI pass is required once interaction is possible.

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

Unlocked full UI testing, installed acceptance and explicit runtime handover
remain. Source publication is limited to the existing authorized branch;
check GitHub CI against its exact commit separately from these local results.
No claim
of profitability, safe copy trading, all-HIG compliance, hardware endurance or
complete accessibility certification is made.

All retained implementation decisions and their risks are in
[Release decisions](2026-09-30-release-decisions.md).
