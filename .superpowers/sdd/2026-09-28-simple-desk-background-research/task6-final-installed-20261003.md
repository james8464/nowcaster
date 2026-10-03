# Final installed acceptance — 3 October 2026

Release verification addendum: code9f4325fbded8968f5e57b4dc831348845056efd2
CI37127295230 completed SUCCESS at14:29:07 UTC. Native172passed39.944s,
deterministicreplay16passed; Python1771passed7skipped32warnings2395.52s,
coverage89%. Skipped packaged-artifact checks are not remote passes; local
signed-helper evidence remains separately attributed. Final14:30 UTC root
read-only ps confirmed the same four PIDs below; latest inspected BTC/ETH
provider14:29 receipts14:29:05.134827/14:29:09.135464 had nullprovidererror.
No restart/duplicate collector or protocol change during the CI wait.
Final receipt publication is Markdown-only with skipped redundant CI; the
successful code CI is explicitly bound to9f4325f.

Current status: installed handover, real lifecycle and corrected new-fixture
synthetic regression PASS. Corrected installed native normal active Quit took
2.895348s under the unchanged30s assertion. Historical old-fixture retry failed
30.779632s and is retained below; that fixture has not resumed. Ordinary
installed launch and final paper-only Start PASS, independently verified.
App34843/collector34995/bootstrap35236/worker35240 intentionally remain alive.
New BTC/ETH receipts observed at13:49 and13:50Z; last snapshot13:50:55Z is
stale/research-control-paused under the unchanged15s guard, not continuous
freshness or active training. Every preference key/value remains unchanged.
No new real campaign registration, settings, daemon or frozen-study changes.

## Final ordinary installed handoff and independent evidence

Ordinary `open -a /Applications/Nowcaster.app` exit0. Fresh observer first
confirmed ordinary app34843 and no helpers before Start. Handoff test
1passed/0failed5.721s, xcodebuild0; log
`/tmp/NowcasterFinalOrdinaryHandoff-20261003-1347.log`, result
`/tmp/NowcasterFinalOrdinaryHandoff-20261003-1347.xcresult`.
Its immediate status was Preparing session, not worker-readiness proof.
Independent observer first saw bootstrap/worker13:49:24.314139Z, within the
existing180s startup bound. No retry/relaunch or manual helper command.

Exact current identities, from independent ps and lsof after observer cleanup:

| Process | PID | PPID | Birth UTC | Working directory |
| --- | --- | --- | --- | --- |
| Ordinary app | 34843 | 1 | 13:47:27 | / |
| Collector | 34995 | 34843 | 13:48:11 | /Applications/Nowcaster.app |
| Bootstrap | 35236 | 34843 | 13:49:24 | /Applications/Nowcaster.app |
| Worker | 35240 | 35236 | 13:49:24 | /Applications/Nowcaster.app |

Collector argv targets real PaperResearch/paper-desk-v1. Both research argv
target real BackgroundResearch/registry, campaignfec5bd… and
run3DD6F9E9-40F2-48CA-953F-87CA865BA14D with workers5. Retained authenticated
ownership pid35240/parent35236/processgroup35240/birthseconds1791035364,
microseconds497274; matching nonce/control. No process cwd is the frozen study.

New provider/receipt evidence (no provider_error):

| Symbol | Provider UTC | Receipt UTC | Provider-to-receipt seconds |
| --- | --- | --- | --- |
| BTCUSDT | 13:49:00 | 13:49:06.138116 | 6.138116 |
| ETHUSDT | 13:49:00 | 13:49:09.140474 | 9.140474 |
| BTCUSDT | 13:50:00 | 13:50:05.142182 | 5.142182 |
| ETHUSDT | 13:50:00 | 13:50:08.139852 | 8.139852 |

Last retained observer snapshot13:50:55.789076Z: providerstale/collectorstale,
last provider13:50:00Z, controlpaused since13:50:20Z. Across the bounded
observation provider states degraded/stale; collector warming/abstaining/stale
(initially stopped). Real research honest idle Waiting for eligible observations,
attempts0/batches0/checkpoints0, no qualification/performance claim. Fresh
receipt advancement does not mean continuously fresh eligibility.

Complete parsed preference objects, including all12 keys and nested source,
equal paper-session.switched.json; raw encoding SHA
`f38b43a4f4dfe44fe43522cfeff05bccd7ca7728d2a473ee0538d12ef93c13bb`
may differ because native serialization rewrites formatting, not values.
Learningtrue/resumefalse/menufalse/efficient/seed42, all activepins exact;
sourceBindings absent, not introduced. Canonical real registry2campaigns/
2records/0activity/0exposures, old4534-byte prefix exact, SHAfb721… unchanged.
Source protocol SHA31382f… unchanged. Installed native74ab723…/engine663f…,
deepstrict verification0 after final Start. Observer34827 terminated through
its own TERM handler; sentinel34835 cleaned, both absent. App/helpers remain
user-owned. No daemon/login/notifications/sleep/security changes.
Source JSONL original byte prefixes were directly asserted by the passed real
XCTest. No separate source-byte snapshot was retained for an independent
all-source-prefix comparison specifically after final Start; do not infer that
additional check from protocol/registry equality. Frozen study was never
selected, read for evaluation or changed by this operator; no new all-study
hash inventory is claimed.

Exact commands, from this checkout with main .venv python:

```sh
python -m tests.background_acceptance_processes /private/tmp/UIAcceptanceFixtures/FinalHandoff-20261003-1347 --seconds 600 --source-directory '/Users/james/Library/Application Support/Nowcaster/PaperResearch/paper-desk-v1' --registry-directory '/Users/james/Library/Application Support/Nowcaster/BackgroundResearch/registry'
open -a /Applications/Nowcaster.app
NOWCASTER_SKIP_ENGINE_BUNDLE=1 TEST_RUNNER_NOWCASTER_UI_FINAL_HANDOFF=1 TEST_RUNNER_NOWCASTER_UI_EXPECTED_CAMPAIGN_HASH=fec5bd5090674eb81a923e994557c7feaa13ef2916f595c843a3478e0cb06afd TEST_RUNNER_NOWCASTER_UI_REAL_ROOT='/Users/james/Library/Application Support/Nowcaster' TEST_RUNNER_NOWCASTER_UI_PROCESS_SNAPSHOT=/private/tmp/UIAcceptanceFixtures/FinalHandoff-20261003-1347/snapshot.json xcodebuild test -project macos/Nowcaster/Nowcaster.xcodeproj -scheme Nowcaster -destination 'platform=macOS,arch=arm64' -derivedDataPath /tmp/NowcasterFinalRelease-20261003 -resultBundlePath /tmp/NowcasterFinalOrdinaryHandoff-20261003-1347.xcresult -only-testing:NowcasterUITests/FinalPaperSessionHandoffUITests/testOptInStartAlreadyOpenedInstalledSessionAndLeaveRunning > /tmp/NowcasterFinalOrdinaryHandoff-20261003-1347.log 2>&1
lsof -a -p 34843,34995,35236,35240 -d cwd -Fn
ps -ww -p 34843,34995,35236,35240 -o pid=,ppid=,lstart=,command=
```

## Corrected native installation and passed regression

Prepared post-build-for-testing native SHA256
`74ab723916b390b2f0ebce81381950e3b83a85d4f634cf321a28352ac58636fa`
(outer re-sign after the earlier c4edf build). All1082 Helpers entries and
engine-manifest bytes match the previous installed app; engine663f/source1c1a/
runtimef96 unchanged. Deepstrict and source/environment verification exit0
before staging and after installation. Old app recoverably moved to
`/Applications/Nowcaster.before-shutdown-20261003-1342.app`; ditto-staged
replacement verified before atomic path moves. No app/helpers before install.
Every real preference key/value equals the switched snapshot, real ledger
SHA fb721704648645bf4935794ecb192aa9f796bfb5d02515a0a8d1bf38922ba3af,
old synthetic ledger6f96849f… unchanged before the new run.

New marked fixture:
`.superpowers/UIAcceptanceFixtures/ShutdownRegression-20261003-1342`.
Prepared with the existing tests.background_ui_acceptance entry point, exit0.
Observer `/private/tmp/UIAcceptanceFixtures/ShutdownRegression-20261003-1342`,
bounded1000s, existing tests.background_acceptance_processes entry point.
Selected unchanged full test log
`/tmp/NowcasterShutdownRegression-20261003-1342.log`, result
`/tmp/NowcasterShutdownRegression-20261003-1342.xcresult`;
1passed/0failed178.600s, xcodebuild0, TEST SUCCEEDED.
Normal active Quit2.8953479528427124s, no installed helpers remain, unrelated
sentinel32910 remained alive. Observer32902 stopped through its TERM handler,
cleaning only its own sentinel.527samples, maxgap0.890575s, sentinel alive in
every sample; lastprocessset empty.

Canonical ledger111records/1campaign/1batch/109events/0exposures:
50reserved,5completed evaluations,11rejected,34interrupted,0failed and
0unresolved results. Five checkpoints at attempts2/3/4/5/6. SHA256
`b7d42278d4920ca188f22386f51affccb50d5ce041e7d34c9be75cb27f92dbf6`.
Campaign34ED6CC0-9900-4988-B3E7-82707EC916DA,
hash83676db6335c8e283faf6b76a5225bb892ebd5513a5db5cebc470e1bb9028795,
batch9fbce6c184785b19b6b057d98016bc460a0f069b92d6819af9e7508a11db94ce,
runtimef96 unchanged. Fresh resume runA20BF52F-C34F-4B09-8223-C9DCDBA8DEE1
differs from initial DC7D266F-C898-4194-B6C5-63BB70F5D0CA, with distinct
nonce/PID/birth; samecampaign/batch and originalledgerprefix preserved.
Protocol/receiptprefix preserved; fixture resume reset normally through UI,
then Pause and Quit. Full real preferences unchanged; old fixtureSHA6f968…
unchanged. This proves authenticated execution resume, not a newly completed
evaluation after resume; the original count-only assertion limitation remains.
Exact commands from this checkout (python is the main .venv executable):

```sh
python -m tests.background_ui_acceptance '/Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.worktrees/research-round-2/.superpowers/UIAcceptanceFixtures/ShutdownRegression-20261003-1342'
python -m tests.background_acceptance_processes /private/tmp/UIAcceptanceFixtures/ShutdownRegression-20261003-1342 --seconds 1000 --source-directory '/Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.worktrees/research-round-2/.superpowers/UIAcceptanceFixtures/ShutdownRegression-20261003-1342/PaperResearch/paper-desk-v1' --registry-directory '/Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.worktrees/research-round-2/.superpowers/UIAcceptanceFixtures/ShutdownRegression-20261003-1342/BackgroundResearch/registry'
NOWCASTER_SKIP_ENGINE_BUNDLE=1 TEST_RUNNER_NOWCASTER_UI_BACKGROUND_SYNTHETIC=1 TEST_RUNNER_NOWCASTER_UI_BACKGROUND_STORAGE_ROOT='/Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.worktrees/research-round-2/.superpowers/UIAcceptanceFixtures/ShutdownRegression-20261003-1342' TEST_RUNNER_NOWCASTER_UI_PROCESS_SNAPSHOT=/private/tmp/UIAcceptanceFixtures/ShutdownRegression-20261003-1342/snapshot.json xcodebuild test -project macos/Nowcaster/Nowcaster.xcodeproj -scheme Nowcaster -destination 'platform=macOS,arch=arm64' -derivedDataPath /tmp/NowcasterFinalRelease-20261003 -resultBundlePath /tmp/NowcasterShutdownRegression-20261003-1342.xcresult -only-testing:NowcasterUITests/BackgroundSessionUITests/testOptInSyntheticTrainingCheckpointAndAuthenticatedResumeWithClosedWindow > /tmp/NowcasterShutdownRegression-20261003-1342.log 2>&1
```

## Read only retained fixture preflight and discarded test

Read-only preflight used production candidate enumeration and canonical ledger
reading without constructors, evaluation, search, locks or ledger writes.
Retained state: 50 reservations (39 unique candidates), 5 completed evaluations,
11 rejected results and 34 unresolved reservations. The actual fitness/hash
winner is attempt1, candidate
`2e141983a0ae8fa1cb03b24fd9944199224385e8dcb5a7432607832f660b3c63`,
fitness0, baseline desk_donchian_breakout_1m, lookback20, null rule.
Production generation2/seed43/incumbent-only with empty parameter grid yields
50 slots, 1 unique candidate, 50 grammar-valid/already-reserved candidates and
0 novel valid/evaluable candidates. Enumeration SHA256:
`f65a5d3bcacca8da2534fdab9e5253512f402df314adf2bce99cde734b05caca`.
Therefore a new completed evaluation cannot honestly be required for this
retained fixture; no remaining budget was consumed to attempt it.

Controller ruled against the new one-off recovery case. Exactly its193 added
lines (case, helpers and CryptoKit import) were removed with apply_patch.
The original BackgroundSessionUITests.swift now matches HEAD byte-for-byte:
working/HEAD Git blob `e506da9719f46acf6ead1ceab52067ba9d5c3436`;
file gitdiffexit0 and gitdiffcheck0. All original assertions are unchanged.
Old fixture ledger SHA256 before/after remains
`6f96849f2c2783f1472df5c22e7ad4d506e897b36c20630e40caa82560ff253f`.
No reset, registration, resumed execution or successful-learning claim.

Historical discarded case build-only command, no UI execution:
`NOWCASTER_SKIP_ENGINE_BUNDLE=1 xcodebuild build-for-testing -project macos/Nowcaster/Nowcaster.xcodeproj -scheme Nowcaster -destination 'platform=macOS,arch=arm64' -derivedDataPath /tmp/NowcasterFinalRelease-20261003`.
Log `/tmp/NowcasterRetainedRecovery-20261003-BuildForTesting.log`, exit0,
TEST BUILD SUCCEEDED. Existing setup/teardown actor-isolation/headermap and
AppIntents warnings retained; no claim of warning-free output. gitdiffcheck0.
The case was compiled but never run, and is now absent. A separate deterministic
CLI test under controller ownership proves new evaluation after interruption;
the existing full UI test does not assert a new completed evaluation on resume.
The planned new-fixture run is a corrected native shutdown regression, not a
replay or reset of the retained negative evidence. Installation/UI release was
subsequently granted as recorded above; the discarded case remains absent.

## Historical synthetic retry failure and read only diagnosis

Log `/tmp/NowcasterFinalSyntheticRetry-20261003-A09BEE4F.log`; result
`/tmp/NowcasterFinalSyntheticRetry-20261003-A09BEE4F.xcresult`. One test failed
211.046s, xcodebuild65, BackgroundSessionUITests.swift281:
XCTest Quit elapsed30.7796319723s exceeds30s. No external prompt this retry.
Root previously observed PaperCreator informational alert through CUA only
on CoreServicesUIAgent and clicked sole OK; action/subsequent AX/screenshot
timed out, dismissal unverified. No permission/security choice. This operator
performed no CUA; one unchanged retry explicitly authorized.

Native Start registered isolated campaign C5435EC3-ABCC-44CF-88EB-F7FC0304749F,
hash2bb08e41b3be5f6b7a63bef9fbf57115011cf1ff2c146c50ddc5759029c94f7b,
batch69ee0ca3abcce3f012803763fe0b8ae146f48ae73c41643ff5bc3ec6028aaf4f.
Canonical retained ledger74records/1campaign/1batch/72events/0exposures:
50reserved attempts,5completed evaluations,11rejected,0failed/interrupted
results,34unresolved reservations. Five checkpoint records; latestattempt6.
Progress/checkpoint happened while window closed; reopen preserved process
identities. Test then reached normal Quit but not authenticated-resume or
post-Quit sentinel assertions. Sparse synthetic progress is not qualification.

Independent timeline: STOP first observed13:01:33.815758Z (control timestamp
13:01:33), collector4896 absent by that sample. App4771/worker5196/training
children remained through13:02:02.880296Z, absent by13:02:03.450440Z.
Bootstrap5189 still present13:02:03.450440Z, absent/allhelpersgone by
13:02:03.995044Z. XCTest synthesized Quit13:01:33.214754 local-log UTC
equivalent; its timer includes event dispatch/polling. Runtime consumed the
full30s grace, then authenticated TERM/KILL, rather than graceful completion.
Durable runDDB08D9A-7865-4F90-BA2A-0613A44FBF07.interrupted.json explicitly
says shutdown_deadline_interrupted; stopped control timestamp rewritten
13:02:03Z. Therefore UI overhead contributes but is not the only cause.

Read-only recommendation: reserve production shutdown headroom under the
existing30s end-to-end bound; retain interrupted receipt/checkpoint/unresolved
reservations for authenticated recovery. Do not relax the UI assertion to
conceal a full-deadline fallback. Root assigned native-only repair separately.
No code changes by operator. Observer4506 and its sentinel stopped normally;
no installed app/helpers/observer remain. Complete real preferences still
equal switched snapshot; real canonical registry2campaigns/2records/0activity.
Ledger-derived training status is retained state, not proof work is running.
Existing missing post-resume completed-work/checkpoint assertion remains.

Retry command is the unchanged synthetic command below with only observer
root/log/result basenames changed to FinalSyntheticRetry-20261003-A09BEE4F.

## Historical first synthetic interruption

Synthetic failure1test/1failure12.097s, xcodebuild65; log
`/tmp/NowcasterFinalSynthetic-20261003-A09BEE4F.log`, result
`/tmp/NowcasterFinalSynthetic-20261003-A09BEE4F.xcresult`.
BackgroundSessionUITests.swift14 explicit interruption monitor failed at
Settings learning click. Normal Escape/Quit teardown completed. At this first
failure, fixture had0campaigns/records/batches/attempts/completions/research failures/exposures,
no checkpoint; observer/sentinel stopped. Recorded XCTest frame identifies
exact prompt: `/tmp/NowcasterFinalSynthetic-20261003-A09BEE4F-prompt.png`.
No failed history discarded, product bug inferred or OS dialog bypassed.

Historical post-blocker verification: every real preference key/value equaled
switched snapshot, no installed app/helpers, installed deep/strict signature
and source/environment/helper manifest exit0, installed native/engine hashes
stilla0521074…/663f0f52…. Root reports fullPython1777passed1386.14s; no broad
rerun by this operator. Synthetic acceptance and final ordinary Start were
still pending then; current completed evidence and limits are recorded above.

## Exact real and synthetic commands

From research-round-2 checkout; `python` below is exactly
`/Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.venv/bin/python`.

```sh
python -m tests.background_acceptance_processes /private/tmp/UIAcceptanceFixtures/FinalReal-20261003-A09BEE4F --seconds 1500 --source-directory '/Users/james/Library/Application Support/Nowcaster/PaperResearch/paper-desk-v1' --registry-directory '/Users/james/Library/Application Support/Nowcaster/BackgroundResearch/registry'
NOWCASTER_SKIP_ENGINE_BUNDLE=1 TEST_RUNNER_NOWCASTER_UI_BACKGROUND_REAL=1 TEST_RUNNER_NOWCASTER_UI_REAL_ROOT='/Users/james/Library/Application Support/Nowcaster' TEST_RUNNER_NOWCASTER_UI_PROCESS_SNAPSHOT=/private/tmp/UIAcceptanceFixtures/FinalReal-20261003-A09BEE4F/snapshot.json xcodebuild test -project macos/Nowcaster/Nowcaster.xcodeproj -scheme Nowcaster -destination 'platform=macOS,arch=arm64' -derivedDataPath /tmp/NowcasterFinalRelease-20261003 -resultBundlePath /tmp/NowcasterFinalReal-20261003-A09BEE4F.xcresult -only-testing:NowcasterUITests/BackgroundSessionUITests/testOptInInstalledRealTenMinuteBackgroundLifecycle > /tmp/NowcasterFinalReal-20261003-A09BEE4F.log 2>&1
python -m tests.background_ui_acceptance '/Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.worktrees/research-round-2/.superpowers/UIAcceptanceFixtures/FinalSynthetic-20261003-A09BEE4F'
python -m tests.background_acceptance_processes /private/tmp/UIAcceptanceFixtures/FinalSynthetic-20261003-A09BEE4F --seconds 1000 --source-directory '/Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.worktrees/research-round-2/.superpowers/UIAcceptanceFixtures/FinalSynthetic-20261003-A09BEE4F/PaperResearch/paper-desk-v1' --registry-directory '/Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.worktrees/research-round-2/.superpowers/UIAcceptanceFixtures/FinalSynthetic-20261003-A09BEE4F/BackgroundResearch/registry'
NOWCASTER_SKIP_ENGINE_BUNDLE=1 TEST_RUNNER_NOWCASTER_UI_BACKGROUND_SYNTHETIC=1 TEST_RUNNER_NOWCASTER_UI_BACKGROUND_STORAGE_ROOT='/Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.worktrees/research-round-2/.superpowers/UIAcceptanceFixtures/FinalSynthetic-20261003-A09BEE4F' TEST_RUNNER_NOWCASTER_UI_PROCESS_SNAPSHOT=/private/tmp/UIAcceptanceFixtures/FinalSynthetic-20261003-A09BEE4F/snapshot.json xcodebuild test -project macos/Nowcaster/Nowcaster.xcodeproj -scheme Nowcaster -destination 'platform=macOS,arch=arm64' -derivedDataPath /tmp/NowcasterFinalRelease-20261003 -resultBundlePath /tmp/NowcasterFinalSynthetic-20261003-A09BEE4F.xcresult -only-testing:NowcasterUITests/BackgroundSessionUITests/testOptInSyntheticTrainingCheckpointAndAuthenticatedResumeWithClosedWindow > /tmp/NowcasterFinalSynthetic-20261003-A09BEE4F.log 2>&1
```

Synthetic collector context/engine preparation preflight both exit0:
`/tmp/NowcasterFinalSynthetic-Context-20261003.log`,
`/tmp/NowcasterFinalSynthetic-Prepare-20261003.log`. Preflight manifest is
separate and unregistered. All original timelines/fixtures/backups/failures
retained. Observers stopped through documented TERM handlers, cleaning only
their own unrelated sentinels. Frozen study, credentials/orders, qualified
alerts, sleep/login/notifications/security settings and daemons untouched.

Historical preparation/transaction notes below record gates as they existed
at each step; those holds cleared before installation and real acceptance.

## Current real acceptance

One selected test passed728.698s, xcodebuild exit0:
`/tmp/NowcasterFinalReal-20261003-A09BEE4F.log` and `.xcresult`.
Closed interval12:34:13–12:44:13Z, same collector97833/bootstrap98048/worker98139,
run B86B62BC-BEB6-4292-AEA3-106B9AB8A81A. BTC10/ETH10 receipts, provider minutes
12:35–12:44Z, zero missing minutes. BTC latency min/median/max
4.115925/5.121191/6.121153s; ETH7.116687/8.1219075/10.120910s.
Observer1091samples,599.739245s coverage,maxgap0.759363s,oneprocessset.
Provider degraded89.651s/stale510.089s; control running104.440s/paused495.299s.
No continuous freshness, real training or qualification claim. Reopen,
Pause10s/noappend, Quit/stopped-relaunch and protocol/allsourceJSONLprefixes
passed. Complete preference keys/values equal switched snapshot; canonical
registry remains2campaigns/2records/0activity. No installed app/helpers after
teardown; observer and its sentinel stopped. Exact commands will be retained
with final serial synthetic/final-Start evidence.

## Historical preparation and handover

Update: native CI37121939670, fixture/cleanliness and source-review gates
cleared; controller authorized retained runtime handover. One signed-helper
preparation and registration exit0. New campaign
`A09BEE4F-F0DE-40B1-B566-EF69008B9B8B`, created `2026-10-03T12:27:58Z`,
hash `fec5bd5090674eb81a923e994557c7feaa13ef2916f595c843a3478e0cb06afd`,
runtime `f96df4fc0cfbb991304c6ff637f4631835a523a12901f882b9027fed8ed5f5d0`.
Every manifest field except campaign ID/date matched the original, including
complete nested source/schedule/search/cost/budget/gate values. New manifest
SHA256 `293cae38c9ad03f2857605dd3a5befc19821f2a10cf0396452624d23d55f8994`.
Canonical ledger now two campaigns/two records, zero batches/events/exposures,
9068 bytes, original4534-byte prefix unchanged. SHA256
`fb721704648645bf4935794ecb192aa9f796bfb5d02515a0a8d1bf38922ba3af`.

Durable receipt written before atomic active-preference switch. Complete
preference comparison changed exactly the five approved active pins, retaining
every other key/value and opt-in. Switched snapshot externally preserved;
prefs SHA256 `b9f71dadcf0d4929e8d69142b9258d7b10cf5f65256841bcedd7b92585048c95`.
The app remains stopped. Installation/launch held while final focused UI build
owns the prepared source bundle; no compatibility claim for old installed app.

Installation update: UI lock released after focused matrix1pass/0fail54.068s.
Fresh pre-install stopped state, switched prefs and canonical zero-activity
ledger/prefix checks passed. Prepared/installed deep-strict signatures and
source/environment/helper manifest verification exit0; installed native/helper
byte comparisons passed. Old app recoverably moved to
`/Applications/Nowcaster.before-final-20261003-A09BEE4F.app`.
Post-test-build installed native SHA256
`a0521074f280094261eaa490ab7683af7b214077f2ac31d4469fa86c069919e2`,
CDHash `c43a93153597e35e45259efcf5e4d3a8653e8922`; engine unchanged663f….
Earlier987f… is the prepared bundle before Xcode test-build re-signing, not
the installed native identity. Real lifecycle acceptance is next; no pass yet.

Independent prepared-bundle verification: deep/strict signature and
source/environment/executable manifest verification exit 0. Source commit
`68f45be73db7668aa69618e0f6c4878b01a15768`; engine SHA256
`663f0f52c510e22fc343559737f8230f435ebeb70da881e87bce77c8e5a9063d`,
native SHA256 `987fd73d4fb7ab0371202b51609feb250d2465c00a35a1f2b2508c9359f6a202`.
Bundle `/tmp/NowcasterFinalRelease-20261003/Build/Products/Debug/Nowcaster.app`;
development team `Z29CKRV8C9`, identifier `com.james8464.nowcaster`.

Real baseline remains stopped: canonical ledger 4534 bytes, one campaign/one
record, zero batches/events/exposures. Preferences `e107e467…`, ledger
`d0ff9779…`, source protocol file `31382f01…` match retained authorization.

Recoverable copies are verified at
`/Users/james/Library/Application Support/NowcasterReleaseHandover/20261003-preflight.Mfnspp/`:
previous signed app, complete preferences, old manifest and exact ledger
prefix. Its `handover-receipt.md` records full hashes, old active pins, final
bundle evidence, transaction boundaries and exhaustive comparison plan.
Backups copy retained files; the installed app and real preferences/registry
are unchanged.

Remaining: controller gate; one recoverable runtime campaign handover; real
600-second closed-window lifecycle; isolated synthetic checkpoint/resume;
ordinary final Start XCTest; independent actual processes/working directories,
fresh receipts, honest research status and complete preference comparisons.
Do not infer training/qualification from Waiting or public receipts. Existing
synthetic test proves authenticated resume and retained history, not new
completed work/checkpoint advancement after resume. Retain prior accessibility,
installed timed-expiry and tampered-checkpoint limitations from Task 6 report.
