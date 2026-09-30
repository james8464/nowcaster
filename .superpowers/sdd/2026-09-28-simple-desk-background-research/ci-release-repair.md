# Narrow CI release repair — 2026-09-30

Base `0a171de0f338bf5466a7fe1404ff218d42e47df5`; controller-authorized ruling in `progress.md`. Implementation is limited to two Swift closure annotations and four deterministic generated fixture files. `final-ui-report.md` separately preserves both the locked failure and unlocked full default UI pass. Controller-owned release checkpoint/decision documentation is excluded from this commit. No push, install, helper rebuild, real campaign change or historical research search.

## Observed RED and cause

Remote CI **36725181861** failed on the exact base revision. Complete retained log: `/tmp/Nowcaster0a171de-CIFailures.log`.

- Native job uses declared **Xcode16.2 / Swift6.0**. `swift test --package-path macos/Nowcaster` fails at `PaperSessionCoordinator.swift:76–77`: conversion of a non-Sendable function to `@MainActor @Sendable (String) -> Void`. These assignments target main-actor callback requirements with default protocol implementations; the older compiler does not infer the closure actor isolation as the local newer compiler does. Explicit `@MainActor` on the two closures preserves isolation/Sendable checking, weak capture and behaviour. No compiler flags, CI toolchain or deployment floor changed.
- Python job's existing `make verify-research-fixtures` generates the deterministic CI profile successfully, then fails its tracked-fixture diff assertion. `src/strategies/pipeline.py:1493` obtains `research_source_hash` for contextual protocol/cohort identities. That hash covers `src`, `config`, `pyproject.toml` and `Makefile`; the checked fixtures retained older source-derived hashes. Independent structural comparisons below confirm no quantitative drift before accepting regeneration. The cleanliness assertion is unchanged.

These existing CI failures are the RED gate; no new production behaviour/test-only bypass was added. Local toolchain is **Swift6.4**, so local green is not falsely represented as a Swift6.0 compiler run. Supported-compiler remote CI remains required after the controller pushes the repair.

## Minimal repair and preservation

`collector.onTermination` and `research.onFailure` now use `{ @MainActor [weak self] … }`. No other Swift source or concurrency API changed.

Original CI fixture directory and complete native demo base were copied before regeneration to `/tmp/NowcasterCIRepair-20260930.xKP4f2/ci-before` and `native-before.json`. Existing CI database artifacts were not deleted; both verification generations used fresh unique databases. Existing `synchronize_snapshot_fixture.py` merged research sections into the saved native base, preserving its other sections. No strategy definition, data, parameter, cost, qualification, budget or gate changed.

## Exact commands/results

Working directory was the current `research-round-2` worktree. `PY` below denotes `/Users/james/Developer/gs-quant-master/alternative-data-earnings-nowcaster/.venv/bin/python` (executable abbreviation only).

```sh
PY -m src.cli strategy research --profile ci \
  --database-url duckdb:////tmp/NowcasterCIRepair-20260930.xKP4f2/research-first.duckdb \
  --output-dir data/research/ci
# exit0; generate-first.log

PY scripts/synchronize_snapshot_fixture.py \
  --base /tmp/NowcasterCIRepair-20260930.xKP4f2/native-before.json \
  --research data/research/ci/nowcaster-snapshot.json \
  --output macos/Nowcaster/Sources/NowcasterApp/Resources/Fixtures/nowcaster-snapshot.json
# exit0

PY -m src.cli strategy research --profile ci \
  --database-url duckdb:////tmp/NowcasterCIRepair-20260930.xKP4f2/research-repeat.duckdb \
  --output-dir /tmp/NowcasterCIRepair-20260930.xKP4f2/ci-repeat
# exit0; generate-repeat.log

diff -rq data/research/ci /tmp/NowcasterCIRepair-20260930.xKP4f2/ci-repeat
# exit0; all three generated files byte-identical

PY scripts/verify_snapshot_fixture_parity.py \
  --python-research data/research/ci/nowcaster-snapshot.json \
  --swift-fixture macos/Nowcaster/Sources/NowcasterApp/Resources/Fixtures/nowcaster-snapshot.json
# exit0; semantic parity adfdbe9bd0c95d30973b7cab52a314b4957b0444df8da63bc4ab4c81451b9499

swift test --package-path macos/Nowcaster
# exit0; 170 tests passed35.471s; /tmp/NowcasterCIRepair-Native-20260930.log

swift test --package-path macos/Nowcaster --filter SnapshotDecodingTests
# after regenerated native resource: exit0; 10 passed1.922s; native-fixture.log

PY -m pytest tests/unit/test_snapshot_fixture_parity.py tests/unit/test_provenance.py -q
# exit0; 8 passed0.63s; python-focused.log

PY scripts/engine_manifest.py --root . \
  --executable /tmp/NowcasterRelease8703-20260930.kmY6fX/Build/Products/Debug/Nowcaster.app/Contents/Helpers/nowcaster-engine \
  --verify /tmp/NowcasterRelease8703-20260930.kmY6fX/Build/Products/Debug/Nowcaster.app/Contents/Resources/engine-manifest.json
# read-only verification exit0; engine-manifest-verify.log

git diff --check
# no findings
```

Unqualified log names above are under `/tmp/NowcasterCIRepair-20260930.xKP4f2`. The first full native suite follows the annotation change; the fixture-focused native run follows the subsequent resource regeneration. No repeat1771-test Python suite was needed/requested for these Swift/generated-identity-only changes. `make verify-research-fixtures` itself was not rerun locally as a third generation: the actual generator was run twice and outputs compared directly; the unchanged CI command remains the remote gate.

## Exhaustive semantic difference

Retained raw diff: `fixtures-exact.diff`. Every changed JSON path and old/new value: `ci-identity-changes.json`. Sorted whole-document comparisons used:

```jq
walk(if type == "object" then
  del(.cohort_id, .contextual_outcome_index_hash, .contextual_protocol_hash)
else . end)
```

After deleting **only those three fields**, old/new CI and old/new native whole-document diffs are empty (exit0, `ci-semantic.diff` and `native-semantic.diff`). Thus all numerical/performance rows, counts, flags, history, strategy parameters and unrelated native base sections are unchanged. CI snapshot changes comprise108 `cohort_id`,36 `contextual_outcome_index_hash`,36 `contextual_protocol_hash` values. Exact distinct mappings:

| Field | Old | New |
|---|---|---|
| cohort_id | `6ca022886b4d3fdfc7e5d17303aca75af6dfc57762fad28a65baa4afea217227` | `0855b76044549ee615670fed5c50b99bd4ff5b4bc73722b7f91fffd36312da99` |
| cohort_id | `8bbeba1678179f291220ab12ee05639aecbffee1644b8fa5aefcb8a0139ba0bf` | `a53349b382b5b9dae558465ca31ea828a9bee0b5861dceacdf30200b3bf6aa1d` |
| cohort_id | `e9401f1d90aeb841fbe87f34a89aa194e750a55f41f1f16a08c8f4c6eb55f036` | `c2273ca973365f8f63207d640e8d98e4e175a119e0480931732f032a9a5b2913` |
| contextual_outcome_index_hash | `149ca99fa42901bd28f4dacbc656e66a83023d9dde65c5b2c4c5a2636d95be1e` | `0c59cd6d08eb1d9afac1199da8b763796f7aa724714bd16b4158d933a90d845b` |
| contextual_outcome_index_hash | `7527b848eead757e94edae90f8018a6e4e8021b77dd9831833c32d1159cbf8e1` | `a025cfd65d7cc46ee159f197add02e6938045d7463ed2f05c0744df27fdddd27` |
| contextual_outcome_index_hash | `95b85ef654cb1ad663726dafead05d761c4ed0522f334f477488c5effaf607e5` | `819ace5ff9307fceb7aaf03a5de46ee17564196e81de787b75a692bf4fb650e6` |
| contextual_protocol_hash | `fc8c05b006067076c838362f40470a42665f7126f08006bbea858a13567ba4d2` | `3d3dfe17ae52f9ffe48bd950b447b2af66881b74f094ea55663d8d2bb74898e6` |

Summary and Markdown change only `code_hash` from `3aa2b3d726b01b0a21a946181de4617097fe81ae9e58b9ee7e69c12205431df4` to `8c33950025fc8a5f912881f5da1aff4ca9211e0a84a2fc9f6985a74d30510c49`, and `semantic_snapshot_hash` from `7e15e2a36a2a51a17b500cc86ae52ee281374b422b8433a2d9ec98a311d24d13` to `fe630433770cce57261a368825bcae3724f8c72e5fcc2b3232f079d82bf8f42c`. Summary normalized by deleting only those two keys has empty diff (`summary-semantic.diff`, exit0). Markdown raw diff consists of those same two lines.

## Identity and remaining gates

Actual `research_source_hash` before and after this repair is unchanged: `8c33950025fc8a5f912881f5da1aff4ca9211e0a84a2fc9f6985a74d30510c49`. Existing signed helper SHA256 remains `20390caa96b6a900bc54642c07ef18daa233db3592a01faf0caad10a2383cb36`, with source/binary manifest verification exit0. No backend source/runtime identity changed; a native app rebuild will still be required to include the annotations and refreshed demo fixture.

Self-review checked the complete scoped diff and exact generated-field mappings. No weakening of Swift isolation, fixture-cleanliness tests, provenance or research rules. UI pass pertains to the prepared8703 app before these declaration/fixture changes; preserved geometry/main-thread warnings are not declared benign or fixed. Await controller's scoped review, native signed build dispatch and successful exact-revision CI. No install/handover is authorized by this local report.

## Test-target follow-on at 6bc5e49

CI run36755555665 passed the fixture gate and compiled production native code, then failed test compilation at `LivePaperSignalOwnershipTests.swift:72`: conversion of an inferred non-Sendable closure to `@MainActor @Sendable (String) -> Void`. RED retained in `/tmp/Nowcaster6bc5e49-NativeCI.log`. The test callback now explicitly declares `@MainActor`, matching the existing service callback contract; assertions, captures, and production code are unchanged. A scoped search of `macos/Nowcaster/Tests` for `on… = {` assignments found only this closure; coordinator test callback properties are declarations, not similarly inferred assignments.

GREEN command: `swift test --package-path macos/Nowcaster`, exit0, **170 tests / 5 suites passed in34.055s**, `/tmp/NowcasterCIRepair-TestClosure-Native-20260930.log`. This is the local Swift6.4 suite; exact Swift6.0 confirmation still requires remote CI. `git diff --check` passed. Self-review confirms the only code change is one actor annotation; the stale UI-report “uncommitted” sentence is corrected. No compiler/check changes, fixture regeneration, runtime change, rebuild, install, UI repetition or real-study mutation.

## Native CI scheduling correction at f4f1f71

RED: `/tmp/Nowcasterf4f1f7-NativeCI.log` compiled all170 tests, then reported four issues in three background lifecycle tests: two100ms shutdowns resumed after5.999/5.997s (required<1s), and the2s checkpoint shutdown returned interrupted. No threshold was changed. Diagnosis: the three tests alone pass0.256s; with four existing fixture-heavy AppModel tests,7 pass11.903s (sampled repeat13.367s). `/tmp/NowcasterCIShutdown-Sampled-20260930.sample.txt` shows synchronous main-thread fixture JSON decoding (435/1601 samples in `fixtureSnapshot`); no synchronous exit wait in that sample. The same7 with `--no-parallel` pass12.586s, lifecycle cases0.168/0.185/0.213s. A process-local strict-cooperative-pool single cancellation probe passes0.181s. Logs: `/tmp/NowcasterCIShutdown-{Isolated,Contended,Sampled,SerialDiagnostic,StrictPool}-20260930.log`.

This supports shared MainActor fixture contention, but the exact remote deadline violation was not reproduced locally; blocking production detached I/O was inspected, not established as this failure's cause. Per controller ruling, only native CI's existing command now explicitly uses `--no-parallel`, with a short rationale. All170 tests, internal concurrent lifecycle exercises, assertions, deadlines, job timeout and toolchain remain unchanged. [SwiftPM's reported help/default discrepancy](https://github.com/swiftlang/swift-package-manager/issues/8174) explains why the flag must be explicit for Swift Testing.

GREEN: `swift test --package-path macos/Nowcaster --no-parallel`, exit0, **170 tests / 5 suites passed44.537s**; `/tmp/NowcasterCIShutdown-FullSerial-20260930.log`. The three cases pass0.167/0.183/0.226s. `git diff --check` passed; self-review confirms CI scheduling/comment and this report only. Exact supported-compiler CI remains the remote gate. No production/Makefile/fixture change, app rebuild, Python rerun or installation. The separately authorized final handoff UI test remains uncommitted and excluded from this repair.
