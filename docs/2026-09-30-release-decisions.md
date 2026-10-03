# Release decisions — 30 September 2026

Complete retained controller rulings from the approved plan ledger, in ledger order.
The ledger contains historical continuation entries out of date order; this is
not a newly inferred chronology. Each entry retains the reason and risk if wrong.
No research attempts, losses, evidence or campaign identities are erased by this report.

## 35 — Keep final evidence publication separate from code verification

Wait for CI37127295230 on code commit9f4325f to finish; do not bypass or cancel
that verification. Once it succeeds, publish only the final Markdown receipts
with a documentation-only `[skip ci]` commit, after checking the exact diff and
remote branch. This avoids restarting the same complete suite solely to record
its result. Clearly attribute CI to9f4325f, not the later documentation commit.
Risk: skipped workflows can leave required checks pending for a future pull
request; no merge is performed here, and any later merge must satisfy its own
required checks. No executable, test, configuration or generated data change is
eligible for this documentation-only exception.

## 34 — Do not manufacture novelty in installed recovery evidence

Read-only production candidate enumeration found zero novel generation-two
candidates in the interrupted synthetic fixture: all fifty slots duplicate its
retained baseline winner. A proposed one-off UI assertion requiring a new
completion would therefore be impossible. Remove only that newly added, unrun
test; retain its diagnosis and the original fixture unchanged. Rerun the existing
unchanged installed lifecycle scenario in a new marked synthetic fixture after
the native shutdown correction. It verifies checkpoint/ownership/resume/Quit,
not a new evaluation after resume. The separate deterministic CLI regression
verifies actual newly completed recovery work. Risk: these are separate evidence
layers; report them separately and never call exhausted candidates new learning.

## 33 — Make the recovery-test precondition deterministic

CI37123233207 failed the resumed-worker WAITING predicate. An isolated unchanged
engine reproduction reserved all four fixture attempts, stopped successfully,
retained all four as interrupted, then authenticated resume correctly reached
FAILED in 3.10 seconds because no evaluable candidate or unreserved attempt
remained. The test would wait its full 90 seconds for an impossible WAITING state.
This proves a fixture race; attribution to that exact CI run remains an inference
because the old failure output omitted worker events. Permit a test-only repair
that gates immediately after the first durable reservation until the parent's
real TERM/STOP, retaining three unreserved attempts and asserting a genuinely new
completion. Keep the four-attempt fixture, deadline, history and production budgets intact.
Retain the exhausted-budget reproduction and improve failure diagnostics.
Risk: a test barrier can diverge from production timing; it changes only timing,
uses the real durable append and signal handler, has a bounded failure path,
and is absent from the normal resumed worker. Normal, reduced-core and coverage
conditions must pass before publication.

## 32 — Reserve native application shutdown headroom

The unchanged installed synthetic retry failed its 30-second end-to-end Quit
assertion at 30.779632 seconds. Process observations and the durable interruption
receipt confirm the worker consumed its full 30-second grace before authenticated
deadline cleanup; this was not merely an assertion timing artifact. Preserve the
failed run, completed outcomes and 34 unresolved reservations. Permit a minimal
native-only drain-budget correction that reserves application cleanup headroom
inside the existing outer 30-second contract. Keep the UI assertion, ownership
checks, durable STOP, interruption receipts and research protocol unchanged.
Require failing regression evidence, full native verification, scoped review,
and corrected installed checkpoint/resume acceptance. Risk: less graceful drain
time can interrupt more in-flight work; retained authenticated recovery must pass.

## 31 — Narrow informational-alert dismissal

The installed synthetic test stopped before registration at an unrelated
CoreServicesUIAgent alert: “The application ‘PaperCreator’ is not open anymore.”
The controller verified that exact text and sole OK button using native
accessibility, then attempted only its non-binding dismissal. The action and
subsequent observations timed out, so dismissal was not assumed successful.
Permit one unchanged synthetic retry with a fresh bounded observer; preserve
the failed run and stop on any remaining unknown, permission or security prompt.
No permission, security setting, document or research state is changed.
Risk: the obstruction may remain; the existing interruption monitor stays intact.

## 30 — Bounded large-text UI readiness

Add explicit activation and the same existing ten-second geometry/hittability
readiness bounds to the large-text test phase. Preserve all original assertions,
seven text-size shortcuts, the 200% setting check and ordinary Quit. Store
readiness outcomes before diagnostic capture so the capture cannot rescue a
missed deadline. Two earlier runs failed differently; the diagnostic run and
final focused test passed, but the original cause remains unproven. No production
layout fix is claimed. Risk: waiting can hide transient behavior; retain strict
bounds and both failed result bundles, plus future failure screenshots/AX data.
Scoped review approved the correction. Minor deferred: the compact diagnostic
status label is blank, but the full accessibility attachment retains its value.

## 29 — Reduced-core Python release failures (3 October)

Revalidate ownership before publishing each completed evaluation, including the
last result. Deterministic reduced-core tests reproduced writes after ownership
loss; this is a release blocker, not a reason to weaken evidence assertions.
Reduce only the synthetic recovery test budget from20to4 attempts, retaining
the90second deadline, real evaluation and immutable-prefix checks, and assert
resumed completed results. The old test completed10of12 evaluations by82seconds
with one worker but exceeded its deadline while still working. Risk: a smaller
fixture covers fewer candidates; retain separate budget and ownership tests.
Production budgets and research gates remain unchanged. Require scoped review,
fresh source-derived fixtures, packaged helper and final verification.

## 26 — Controlled native test scheduling

Run all native CI tests explicitly serially, retaining every assertion, deadline
and concurrent operation inside each test. The CI compiler now succeeds, but
three lifecycle tests miss deadlines while unrelated large fixtures occupy the
shared main actor. Local sampling confirms that contention; serial comparison
removes it, although the exact remote failure was not reproduced locally.
Risk: serial scheduling loses incidental cross-test stress. Keep the dedicated
concurrency tests and installed background checks; do not claim arbitrary-load
responsiveness. No application, runtime, toolchain or timing threshold changes.
Swift Testing's shared-process parallel default and explicit serial option are
documented by [Apple](https://developer.apple.com/documentation/Testing/Parallelization).

## 27 — Preserved runtime handover

Conditionally authorize recoverable installation and one new runtime-pinned
campaign in the same registry after review, signature and required CI gates.
The old runtime cannot safely run the corrected app; keep its campaign, manifest,
history and complete ledger prefix. New manifest fields must match the old
manifest except new ID/actual creation time, with the final runtime bound at
registration. Back up the app/preferences; write a durable comparison receipt
before switching only active campaign pins. Risk: accidental evidence reset or
displacement of active work. Abort on unexpected activity, hashes or field
differences; rollback active app/preferences without deleting registrations.
No strategy rule, schedule, cost, budget, qualification or opt-in changes.

## 28 — Normal final Start interaction

Permit a separate, default-skipped XCTest handoff action to press normal Start
on the already ordinarily launched installed app, after checking its stopped
state and expected campaign pin. This restores the approved paper session
without silently enabling automatic resume/login. Risk: unintended persistent
work. Require explicit opt-in, unchanged preferences, no pre-existing owned
helpers, visible state and independent PID/fresh-receipt checks afterward.
Existing lifecycle-test teardowns remain unchanged. The handoff test deliberately
leaves only the user-owned app/session running; it does not place orders.

## 25 — CI release compatibility

Permit a narrow repair of newly observed CI release failures, not another
whole-branch review/fix wave. Published code must build with the declared
supported compiler and reproduce its checked fixtures. Keep Swift concurrency
checks, the CI toolchain and all fixture/evidence gates. Isolated regeneration
showed only source-derived identity changes, not changed numerical outcomes.
Risk: refreshing fixtures could conceal behavioral drift or callback annotations
could weaken isolation. Require semantic comparison, regression verification,
one scoped review and terminal CI for the repaired commit. No strategy search,
real-campaign mutation, evidence reset or qualification change is authorized by
this decision.

## 1

Ruling: If complete source verification passes while the desktop remains locked, push a clearly labeled checkpoint to the already-authorized feature/research-round-2 branch and verify its CI, without installing or declaring release completion — this preserves the requested one-branch GitHub work while native interaction requires user action — cost if wrong: readers might mistake published source for installed/UI-verified readiness; checkpoint documentation and final response must explicitly retain the UI blocker, stopped old installation, immutable old campaign and outstanding acceptance/handover gates. No PR, merge, force push or new branch.

## 2

Ruling: Permit the Task2 lineage fix to minimally extend the existing candidate generator if required, preserving its default behavior — the approved second generation must derive from the selected winner, while the general generator intentionally mixes unrelated exploration — cost if wrong: existing research candidate sequences could change; test both parameter/rule ancestry, deterministic recovery, grammar bounds and legacy defaults in scoped tests.

## 3

Ruling: Make src/research/__init__.py public exports lazy and defer runtime data/trainer imports until after ownership — startup tracing found package initialization eagerly loading full-history/live-signal scientific dependencies before registration or ownership (packaged run~28s to first ownership; first registration timed out60s, second completed) — cost if wrong: public imports or multiprocessing resolution could regress; preserve names/__all__/identity and test legacy facade plus scipy/sklearn-blocked registration, numeric limits before heavy imports, and real packaged first-event/cancellation timing. No broad module refactor or dependency change.

## 4

Ruling: Apply the same narrow lazy-export preservation to src/learning/__init__.py — import-blocker tracing also found contracts→candidates→learning.grammar→learning facade→promotion/search→SciPy before ownership — cost if wrong: legacy learning imports could regress; preserve/test both public facades and complete import-path investigation before any further scope extension. This remains the same startup-cause fix, not a broad learning rewrite.

## 5

Ruling: Add a narrow backend prepare-background-research command to the existing lightweight helper for first opt-in — Swift would otherwise duplicate canonical protocol hashing, Python validation and strategy-specific search grammar merely to offer beginner-friendly setup — cost if wrong: extra CLI surface or inappropriate search defaults; accept explicit source/output/campaign-ID/seed/UTC-created-at, validate/protect paths, copy source costs/schedule/gates exactly, derive only deterministic supported candidate spaces, retain all choices in an immutable manifest before evaluation, reject unknown families and changed-output overwrite. Native reuses manifest/hash on retry/resume/profile change; registration remains authoritative for runtime identity. No new helper/dependency, active-strategy mutation, automatic qualification or data-dependent tuning. Targeted Python tests and final helper smoke; full release suite remainsTask6.

## 6

Ruling: Permit optional expected-campaign-hash and expected-runtime-code-identity checks on repeat registration, before registry mutation — same-ID runtime changes already fail in registry.py318–333, but changed/tampered manifest ID/path could otherwise register another identity before Swift rejects its reply — cost if wrong: extra CLI surface could reject a legitimate retry; native repeats pass both retained hashes, first registration retains existing behavior, and tests prove mismatch preserves source/registry bytes. Persist the original manifest/campaignID before launch so cancellation/lost reply never chooses a new ID. No identity weakening or silent re-registration.

## 7

Ruling: Hold runtime-only capacity preemption until verifiable native pressure recovery or explicit retry/profile adjustment, with a clear resource-review reason — existing guard uses payload size versus70%physical memory or an estimated checkpoint disk reserve; generic native healthy booleans cannot prove those batch-specific conditions recovered, and blind retries could consume retained attempts without useful computation — cost if wrong: research may remain held after unobserved capacity recovery, requiring user retry. Ordinary native thermal/power/memory/disk/collector pauses still resume on observed recovery; user Pause never auto-resumes. Preserve detailed runtime reason, test no healthy-poll retry churn and fresh-execution explicit recovery without evidence reset. No guard or qualification redesign.

## 8

Ruling: Remove native directory-wide collector Stop entirely; interrupt only the retained launched Process, with bounded owned TERM/KILL fallback — existing lock has no acquisition nonce/PID, while runner.start catches KeyboardInterrupt inside its held lock and performs graceful stop; before acquisition SIGINT cannot write that stop — cost if wrong: a nonresponsive collector may require forced termination and leave stale backend state until expiry. Local stopped/interrupted state must immediately mask actions and never claim an unreceived acknowledgement. Apple Process.interrupt primary documentation confirms SIGINT to receiver/subtasks; check launched/isRunning, keep existing identity/30s deadline, test external lock-holder survives/no shared STOP write plus own graceful exit. No new backend stop protocol, names or guessed groups.

## 9

Ruling: Correct declared indicator materialization using existing frozen strategy definitions, not new strategy research — independentdiagnostic reproduced six unavailablefeature errors, so retaineddonchian failure cannot be called normal unsuccessfulresearch — cost if wrong: different indicator timing/warmup could leak future data or alter rulemeaning. Require causal prefix invariance, every declaredfamily and actualpreparedcandidate evaluation, preserve all oldfailures/codepins, separate futurecampaign identity. No historicalsearch/gatechange.

## 10

Ruling: Treat explicit stopped source choice as a coordinated per-source binding transition, retaining A→B→A campaign identities — importercurrentlychangesonlycollector whileStartstillselectsA, so advertisedfolderselectionbreaks afterfirstsession — cost if wrong: accidental campaignreset or silentrestoreidentitybypass. Retain old bindings/history in same registry, reject active/draining changes, rollback failed selection, automaticrestore remainsstrict. This repairs existingfeature, no implicit runtime migration.

## 11

Ruling: Extend minimal optional ownership verification into registry/scheduler commit transactions and every coordinator submission/retry — review shows guard-before-blocking-lock and unguardedretry can outlive campaignownership — cost if wrong: valid callers may deadlock or lose orderlyPause/Stop behavior. Default callbacks preserve existing callers, invoke aftertransactionlock immediatelybeforemutation, enforce beforeinitialandretrydispatch, regressiontest blockedwriterreplacement and in-flightfailureloss. No reset/rule/environment changes. New helperruntime will invalidate currentcampaign's pinnedruntime; preserve it unchanged and do not silently re-register/reset to restoreactivity. Defer installedreplacement until final fixes/review are settled; assess explicit retained migration separately, not part of lock fix.

## 12

Ruling: Permit a bounded external read-only process sampler for native XCTest acceptance — runner sandbox rejects Process.run(/bin/ps) with EPERM, and try? falsely converted unavailable observation into zero children; dedicated probe failed0.557s at /tmp/NowcasterTask6ProcessProbe-20260930.log while external observation saw sentinel70905 — cost if wrong: stale/misattributed samples could falsely pass lifecycle checks. Require fresh timestamps and exact PID/birth/argv, explicit unavailable-observation failure, only app-owned/sentinel rows, no external app control, stop sampler after runner. RealFinal readiness failure188.742s and Paused/stale screenshot remain retained independent of this test defect; no live soak success inferred and same new campaign must be reused.

## 13

Ruling: Extend existing runtime lock with held-descriptor/path identity validation at acquisition, control iterations including STOP, dispatch and progress boundaries — Task6 explicitly requires visible lost-lock failure, and RED proves current descriptor-only flock misses pathname replacement — cost if wrong: valid research may stop prematurely or an obsolete owner may overwrite another owner's evidence. After loss do not append shared batch states/results/interruption under obsoleteownership; emit execution-specific blocked error and stop only owned children, preserve registry records and competing owner. Verify scoped unlink/replacement/link/no-dispatch cases, rebuild signed helper with new runtime identity and rerun full Python once after final backend changes. Do not change campaign rules/identities or frozen source; no existing real learning campaign was started.

## 14

Ruling: Retain the audited dummy-credential history and exempt only its exact historical blob ID, path, SHA256 content digest and line-specific finding; change current fixture to explicit placeholder — otherwise this plan's test fixture falsely blocks CI, while rewriting history or exempting generic literals/test files would be broader and unsafe — cost if wrong: an incorrectly audited immutable finding could be hidden. Independent credential patterns, changed blob/content/path and all current files must still flag; focused regression checks cover these boundaries and the deleted-secret history test remains active. Exact immutable finding contains only the documented dummy literal, not an account credential.

## 15

Ruling: Use one stable two-column NavigationSplitView/sidebar with conditional native HSplitView for the existing inspector routes — actual keyboard tests show2→3-column outer branch replacement recreates the sidebar and loses focus; both same-value and false→true FocusState restoration failed, so stop patching focus — cost if wrong: Advanced inspector sizing/navigation could regress. Preserve content width bounds, destination titles and all routes; remove unsuccessful focus patches, verify primary arrow sequence, Markets and representative Advanced inspector/reachability. No global key interception or private responder workaround. This is within the approved native resizable-inspector design, which does not require three outer columns.

## 16

Ruling: Interpret approved820×620 minimum as outer window dimensions, not content height — actual screenshot measures820×672 because title/toolbar adds height — cost if wrong: smaller content area can crowd controls; require actual frame-size assertions, inspect captures and retain scrolling/access to every action, avoid a universal hard-coded toolbar offset. This is a minimal Task5 window/layout correction under the approved scope.

## 17

Ruling: Permit minimal explicit UI-test storage-root injection with path validation — PaperSessionPreferenceStore.application currently uses real user ApplicationSupport, so toggling learning/resume/menu in XCTest could modify user preferences or resume an existing desk — cost if wrong: test/default storage paths could diverge or unsupported roots could escape isolation; keep installed default unchanged, use marked UIAcceptanceFixtures, isolate preferences plus generated manifests/registry/control receipts, reject protected paths, test the boundary and carry it into installed acceptance. No gate/clock/profit fabrication or study access; this is storage isolation, not a second runtime implementation.

## 18

Ruling: Permanently broken worker output must trigger authenticated orderly STOP rather than indefinite detached dispatch — self-review confirmed an app crash could otherwise leave a campaign lock and orphan worker with no reattachment API; 'broken pipe cannot erase results' requires preservation, not continuing unsupervised work — cost if wrong: intentional output disconnection stops useful research; preserve completed results, mark remaining reservations interrupted, release lock/join children, test transport-loss exit. Normal window closure leaves app/output alive. Apply after current full suite with focused amended coverage, no second full rerun.

## 19

Ruling: Add an optional bounded execution worker-count override through Task3 runtime/CLI/trainer for Task4 resource preferences — the approved spec requires manual performance controls, but the planned interface otherwise leaves the trainer hardcoded to efficient mode — cost if wrong: UI could overcommit resources or a profile switch could bypass recovery identity; default remains half cores reserving at least two, maximum reserves two where available, one numerical thread each; record count in execution protocol, profile changes require a fresh execution preserving batch/trials/exposures. No campaign/search/budget change.

## 20

Ruling: Permit a minimal typed retained-observation mode in src/deep_research/evaluation.py beyond Task2's enumerated files — existing CandidateEvaluationPayload assumes provider-time opens, while round_two_walkforward already implements receipt-causal long-only next-observation execution and source/cost gates; reuse that path rather than shift timestamps or duplicate execution — cost if wrong: regression or coupling in existing evaluators; require compatibility and delayed/revised-prefix tests and scoped review.

## 21

Ruling: Distinguish completed immutable batches from the user-facing Waiting state with an explicit validated completion marker, extending registry/scheduler minimally — otherwise exposed holdouts or permanently sparse frozen prefixes leave the scheduler forever resuming a finished batch; subsequent batches still require original schedule/day/fingerprint and exposure constraints — cost if wrong: premature advancement could skip recovery; require terminal-wait versus interrupted-wait tests, never infer completion from counts. Known insufficient training history waits before candidate attempts; insufficient validation retains performed training and locked selection. Paused/blocked remain non-dispatchable. No prefix reset or holdout reuse.

## 22

Ruling: Permit optional retained metrics in worker.py and a parent-side durable result callback on DeepResearchCoordinator — otherwise real receipt replay occurs serially and only cheap scoring uses the worker pool, defeating the approved resource-limited parallel research model — cost if wrong: callback ordering could lose or duplicate attempt outcomes; reserve before dispatch, persist terminal outcome before checkpoint/progress, stop on persistence failure, and test crash/resume identities. Existing callers keep defaults; workers never write the registry.

## 23

Ruling: Separate authenticated execution/control identity from immutable research batch identity — Quit makes the current execution STOPPED, so reopening must use a fresh run ID/nonce and derived execution index while retaining the original batch, reservations, results, failures and exposure receipts; never reopen/reset terminal control — cost if wrong: reconstruction could duplicate trials or reclaim another worker; retain the batch-wide lock and add stopped/new-control resume and terminal-control rejection tests. Task3 authorizes execution/content identity; Task2 restores only authenticated retained attempts.

## 24

Ruling: Task3 may minimally extend scripts/live_engine_entry.py and existing manifest/embed packaging as required — the shipped helper currently accepts only monitor commands, excludes research dependencies, and reuse regenerates a source manifest without proving binary parity; src/cli.py changes alone cannot deliver the approved packaged worker — cost if wrong: a broader package or stale identity could regress existing monitoring or certify the wrong executable; preserve narrow lazy dispatch, verify actual signed binary/manifest and run real packaged research plus existing monitor regressions. No new helper or daemon.
