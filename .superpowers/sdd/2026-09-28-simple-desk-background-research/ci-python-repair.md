# Narrow Python CI repair — 2026-10-03

Base `88bf21d`, branch `feature/research-round-2`; controller ruling in `progress.md`. No commit, push, installation, real registry/study change, frozen-study access or historical search by this agent. Controller-owned release documentation and untracked `FinalPaperSessionHandoffUITests.swift` are untouched.

## Cause and repair

CI36757730841 passed native tests but failed two Python tests (1762 passed, 7 skipped). Retained remote log: `/tmp/Nowcaster88bf21d-CIFailure-20261003.log`.

1. **Lost ownership after evaluation:** coordinator authority was checked at submission boundaries only. With three CPUs, the two-CPU reserve caps the executor at one worker; the first result was persisted before the next submission detected lost ownership. Loss during the final evaluation was never detected. Four added production lines read the authenticated control before each result callback and before repository publication. The returned STOP state does not discard authorized completed results; invalid ownership raises before publication. Tests cover initial submission, retry, final evaluation and loss in the first publication callback, each at effective worker counts 1 and 2. They require the replacement owner to remain valid and prohibit stale callbacks, trial rows and checkpoints.
2. **Lifecycle fixture exceeds deadline:** unchanged real subprocess test with one resumed worker evaluated 10 of 12 unique candidates at 82.182 seconds, then exceeded its 90-second wait while still running. This is real workload cost, not a stuck worker. Only this synthetic campaign's attempt budget changes from 20 to 4; the production budget and 90-second deadline are unchanged. Existing batch identity, exact attempt prefix, old control bytes and old database bytes assertions remain. Added assertions require exactly four attempts/results, matching IDs, and a newly completed result after relaunch. Separate 100-attempt/two-generation budget coverage passed in the training suite.

## RED evidence

All commands ran from the worktree; `PY` denotes `/Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.venv/bin/python`.

```sh
PY -c 'import os, pytest; os.cpu_count=lambda:3; raise SystemExit(pytest.main(["tests/integration/test_deep_research_coordinator.py::test_ownership_loss_prevents_every_submission_and_leaves_replacement_owner_intact", "-q"]))'
# Original test: 1 failed / 1 passed, 2.78s; identical persisted-trial failure.

PY -m pytest tests/integration/test_deep_research_coordinator.py -k ownership_loss -q
# Expanded pre-fix regression: 3 failed / 3 passed, 3.48s.
# /tmp/NowcasterCIOwnershipRed-20261003.log

PYTHONPATH=. PY /tmp/nowcaster_ci_resume_diagnostic.py
# Original unchanged test, forced parent CPU count3:90s timeout.
# /tmp/NowcasterCIResumeDiagnostic-20261003.log
```

The diagnostic logs complete subprocess events and retains its unique synthetic directory. Last progress was 10/12 at82.182s; `poll()` remained `None`. Cleanup killed only the authenticated owned test process group.

An in-memory mutation removed only the new per-callback read (the on-disk source was unchanged) using `inspect.getsource`, `textwrap.dedent`, and `exec` into the module globals; pytest selected `ownership_loss and publication`. Result: **1 failed / 1 passed**,0.79s; two-worker publication incorrectly produced `[1,2]` instead of `[1]`. `/tmp/NowcasterCIPublicationMutationRed3-20261003.log`. Two earlier mutation harness attempts were invalid (copied globals bypassed patched evaluation; incorrect indentation failed the harness assertion); their logs are retained as `MutationRed` and `MutationRed2`, not claimed as behavioral evidence.

## GREEN verification

```sh
PY -m pytest tests/integration/test_deep_research_coordinator.py tests/integration/test_background_research_training.py -q
# 40 passed187.73s; /tmp/NowcasterCIPythonFocusedGreen-20261003.log

PY -c 'import os, pytest; os.cpu_count=lambda:3; raise SystemExit(pytest.main(["tests/integration/test_deep_research_coordinator.py", "-q"]))'
# 25 passed19.48s; /tmp/NowcasterCICoordinatorLowCoreGreen-20261003.log

PY -c 'import os, pytest; os.cpu_count=lambda:3; raise SystemExit(pytest.main(["tests/integration/test_background_research_cli.py::test_term_checkpoint_and_authenticated_relaunch_keep_batch_and_attempt_prefix", "-q"]))'
# 1 passed39.11s; /tmp/NowcasterCIResumeLowCoreGreen-20261003.log

PY -m pytest tests/integration/test_background_research_cli.py::test_term_checkpoint_and_authenticated_relaunch_keep_batch_and_attempt_prefix -q
# 1 passed36.09s; /tmp/NowcasterCIResumeGreen-20261003.log

PY -m pytest tests/unit/test_snapshot_fixture_parity.py tests/unit/test_provenance.py -q
# 8 passed0.91s; fixture-tests.log below
```

Earlier coordinator-only run passed23tests21.75s before the two publication callback cases were added; the40-test final run includes all25 coordinator cases. Scoped Ruff formatting/checks and `git diff --check` passed. Full suites/package/remote CI remain controller gates; none are represented as completed by this report.

## Deterministic fixture refresh and exact preservation

Artifacts directory: `/tmp/NowcasterCIPythonRepair-20261003.3CuRnW` (`D` below). Original CI files and native snapshot were copied into `ci-before` and `native-before.json`. Both generations use fresh unique databases; no existing database was removed.

```sh
PY -m src.cli strategy research --profile ci --database-url duckdb:////tmp/NowcasterCIPythonRepair-20261003.3CuRnW/research-first.duckdb --output-dir /tmp/NowcasterCIPythonRepair-20261003.3CuRnW/ci-first
PY scripts/synchronize_snapshot_fixture.py --base /tmp/NowcasterCIPythonRepair-20261003.3CuRnW/native-before.json --research /tmp/NowcasterCIPythonRepair-20261003.3CuRnW/ci-first/nowcaster-snapshot.json --output /tmp/NowcasterCIPythonRepair-20261003.3CuRnW/native-after.json
PY -m src.cli strategy research --profile ci --database-url duckdb:////tmp/NowcasterCIPythonRepair-20261003.3CuRnW/research-repeat.duckdb --output-dir /tmp/NowcasterCIPythonRepair-20261003.3CuRnW/ci-repeat
diff -rq /tmp/NowcasterCIPythonRepair-20261003.3CuRnW/ci-first /tmp/NowcasterCIPythonRepair-20261003.3CuRnW/ci-repeat
# All exit0; both generations byte-identical.

PY scripts/verify_snapshot_fixture_parity.py --python-research data/research/ci/nowcaster-snapshot.json --swift-fixture macos/Nowcaster/Sources/NowcasterApp/Resources/Fixtures/nowcaster-snapshot.json
# exit0; semantic parity36a3f638f00e9aea36c6ced907cc9675d88fe9a369e2ffd376a6a02346078284
```

Before copying the generated files into the tracked fixture locations, whole old/new CI and native documents were normalized with `jq -S 'walk(if type == "object" then del(.cohort_id, .contextual_outcome_index_hash, .contextual_protocol_hash) else . end)'` and compared. Both diffs are exactly empty: `ci-semantic.diff`, `native-semantic.diff`. Every numeric field, candidate/status, count, parameter, cost, gate and unrelated native section is unchanged. CI changed paths are exactly108 `cohort_id`,36 `contextual_outcome_index_hash`,36 `contextual_protocol_hash`; `ci-identity-changes.json` records every path and old/new value. `fixtures-exact.diff` retains the complete raw CI diff.

Summary documents normalized by deleting only `code_hash` and `semantic_snapshot_hash` have empty `summary-semantic.diff`. Markdown differs on those same two hash lines only. Source hash changes from `8c33950025fc8a5f912881f5da1aff4ca9211e0a84a2fc9f6985a74d30510c49` to `df6348b7143ed7be4f5817da5008004a7aed3ca67b885a7e5cc8ab3ba5797137`; snapshot hash changes from `fe630433770cce57261a368825bcae3724f8c72e5fcc2b3232f079d82bf8f42c` to `6a3e16daa80822d08bd2829d819f2d54487d5297c78140221875028399b34db7`. Pydantic validation confirmed schema5.

Controller must rebuild/verify the helper because source identity changed. Existing packaged/installed binaries are not evidence for this repair. Scope remains the single coordinator guard, the two test files, four generated fixtures and this report. No profitability, installed acceptance or continuous-feed claim.
