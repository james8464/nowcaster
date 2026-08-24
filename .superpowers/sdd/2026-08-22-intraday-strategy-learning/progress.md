# SDD ledger — plan: docs/superpowers/plans/2026-08-22-intraday-strategy-learning.md

Merge base: `31f8afb39d259ba6f445cef2767970799fae9870`

Baseline: Python `118 passed`; Ruff `All checks passed`; Swift `18 tests` plus `AppIconTests`, zero failures.

## Preflight interface scan

| Producer task | Consumer task | Shared file/interface | Check |
|---|---|---|---|
| 1 | 2 | `BarInterval`, `market_bars` | Consistent: provider/store consumes versioned interval and timestamped table. |
| 1 | 3 | `StrategySpec`, `StrategyRegistry` | Consistent: generators are resolved from validated registry entries. |
| 2 | 3 | `MarketBar` frames | Consistent: normalized closed bars are ordered and UTC. |
| 2 | 9 | dataset cache/manifests | Consistent: Task 9 stores compact manifests, not bulk bars. |
| 3 | 4 | `StrategySignalFrame` | Consistent: signal timestamps feed the next-bar execution engine. |
| 3 | 6 | indicators and grammar operands | Consistent: learner consumes the same causal feature surface as fixed rules. |
| 4 | 5 | `IntradayBacktestResult` and robust metrics | Consistent: evidence weights consume net walk-forward results. |
| 4 | 6 | execution fitness | Consistent: candidate fitness includes the same costs and fills. |
| 5 | 6 | nested validation/promotion | Consistent: learner is contained entirely inside inner folds. |
| 1–6 | 7 | persisted strategy/learning records | Consistent: snapshot v2 reads stable IDs, versions, boundaries, and timestamps. |
| 7 | 8 | snapshot v2 and typed CLI | Consistent: Swift mirrors Python DTOs and request arguments. |
| 7–8 | 9 | generated fixture/reports/docs | Consistent: Task 9 syncs and verifies the cross-language contract. |
| 1–9 | 10 | complete release candidate | Consistent: final review and verification are branch-wide. |

## Per-task internal scan

| Task | Tests vs implementation | Files vs later consumers | Result |
|---|---|---|---|
| 1 | Registry/schema tests precede contracts/tables | Stable base interfaces | Clean |
| 2 | Parser/store tests precede providers/repository | Supplies causal bar frames | Clean |
| 3 | Literal indicator/rule tests and prefix audit precede library | Supplies signals/grammar features | Clean |
| 4 | Fill, portfolio, and statistics tests precede simulator | Supplies evidence records | Clean |
| 5 | Holdout/weight/current-signal tests precede orchestration | Supplies learner/pipeline decisions | Clean |
| 6 | Grammar/search/promotion tests precede learner | Supplies learning DTOs | Clean |
| 7 | CLI/snapshot tests precede integration | Supplies Swift contract | Clean |
| 8 | Swift decoding/request/presentation tests precede UI | Supplies verified app surfaces | Clean |
| 9 | End-to-end test precedes network research/docs/cleanup | Supplies release artifacts | Clean |
| 10 | Review and fresh verification precede merge/push | No downstream consumer | Clean |

Task 1: minor (deferred): schema-version select-then-insert can race under concurrent initialization.
Task 1: minor (deferred): legacy migration fixture does not reconstruct the exact pre-v2 schema.
Task 1: Ruling: aggregate family-cap enforcement and equal-weight shrinkage belong to Task 5's ensemble write path, which does not exist in Task 1; Task 1 will add nonnegative database constraints and complete config immutability/cap validation now — if wrong, Task 5 may need to move enforcement into an earlier repository layer.
Task 1: fix round 1/5 (3 addressed, 0 open; commits d41d8b1..7bb635d)
Task 1: complete (commits 31f8afb..7bb635d, review clean)
Task 2: minor (deferred): `Retry-After` parsing accepts only numeric seconds and does not cap server-requested delay.
Task 2: fix round 1/5 (2 addressed, 0 open; commits b5172a4..a3d7c7a)
Task 2: complete (commits 7bb635d..a3d7c7a, review clean)
Task 3: minor (deferred): last-half-hour mask includes a bar opening exactly at the 16:00 close boundary.
Task 3: minor (deferred): rolling pair residual function name can be mistaken for a cointegration diagnostic.
Task 3: fix round 1/5 (5 addressed, 1 open — volatility-scaled trend can treat an undefined zero-volatility indicator as valid; commits 9f0cd21..06a8c4e)
Task 3: minor (deferred): auxiliary universe mapping can override the focal primary frame because `setdefault` preserves the caller entry.
Task 3: fix round 2/5 (1 addressed, 0 open; commits 06a8c4e..7069861)
Task 3: complete (commits a3d7c7a..7069861, review clean)
Task 4: minor (deferred): volatility targeting caps scale at 1.0 and therefore cannot lever low-volatility exposure toward target.
Task 4: minor (deferred): cost/bar validation does not reject all NaN/infinite and inconsistent OHLC inputs.
Task 4: fix round 1/5 (4 addressed, 3 open — realized caps across asynchronous/close batches, signed-carry cost stress, and real caller DSR evidence; commits 7cda41d..0861020)
Task 4: Ruling: remove or disable the legacy count-only DSR path instead of fabricating trial variance; the legacy daily pipeline must report DSR unavailable/conservative until Task 5 persists actual trial Sharpes — if wrong, legacy readiness may become more conservative and existing snapshot values may change.
Task 4: fix round 2/5 (3 addressed, 0 open; commits 0861020..418f6ab)
Task 4: minor (deferred): malformed observed trial-Sharpe vectors are not validated when observations are below three.
Task 4: complete (commits 7069861..418f6ab, review clean)
Task 5: review found 1 critical, 8 important, and 2 minor issues: unsealed caller-supplied promotion evidence; cross-context feedback; reset adaptive state; missing causal timestamps; mixed ensemble context/symbol substitution; incomplete order-dependent hashes; shallow provenance immutability; malformed-trial aborts; bypassable frozen guard; weak outcome validation; and missing decision-hash execution provenance.
Task 5: fix round 1/5 implemented (11 addressed, pending fresh re-review; commits 19c5553..a759c75).
Task 5: re-review found 5 important open issues: incomplete/self-attested sealed fold plans, same-timestamp partial-batch replay loss, stale decision hashes, discarded composite/close/flatten execution provenance, and naïve timestamp acceptance.
Task 5: Ruling: netted, close, and session-flatten orders require deterministic composite source-decision provenance rather than `None` — if wrong, execution records may become larger and existing ledger/snapshot fixtures may change.
Task 5: fix round 2/5 implemented (5 addressed, pending fresh re-review; commits a759c75..84c3c28).
Task 5: re-review 2 found 5 important open issues: incomplete independent root/promotion audit, unverified persisted feedback/cohort state and as-of rollback, conflicting composite-source identity ordering, reversal provenance contamination, and remaining naïve signal/bar timestamp coercion.
Task 5: fix round 3/5 implemented (5 addressed, pending fresh re-review; commits 84c3c28..570ef77).
Task 5: re-review 3 found 1 critical and 2 important open issues: future validation evidence accepted for earlier `as_of`, self-forgeable embedded validation policy, and unauthenticated current weights at empty-feedback/decision boundaries.
Task 5: fix round 4/5 implemented (3 addressed, pending fresh re-review; commits 570ef77..b5a5647).
Task 5: re-review 4 found 2 important open issues: future-dated frozen weights accepted at combine, and direct `fixed_share_update` still lacked trusted evaluations/policy plus independent mass/cap rederivation.
Task 5: fix round 5/5 implemented (2 addressed, pending final fresh re-review; commits b5a5647..1286860).
Task 5: final re-review found 1 important issue still open: FROZEN mode validates public weight watermarks but skips full persisted `online_state` schema/hash/history replay validation, so a self-consistently rebuilt frozen state can hide a future processed outcome while retaining earlier public timestamps.
Task 5: escalation required after fix round 5/5; implementation paused pending user authorization for an exceptional sixth fix cycle.
Task 5: Ruling: run one exceptional user-authorized sixth fix cycle to reject any persisted online replay state in FROZEN mode and close the load-bearing causality boundary — the finding affects every later learner/current-signal consumer, so parking it would violate the no-repaint specification — cost if wrong: one extra implementation/review cycle and stricter rejection of legacy frozen snapshots carrying online state.
Task 5: exceptional fix round 6 authorized and in progress (1 important open; fix-round-5 commit 1286860).
Task 5: exceptional fix round 6 implemented (1 addressed, pending fresh re-review; commits 1286860..16ce12f).
Task 5: minor (deferred): authenticated exact online replay retains growing canonical outcome history; future compaction requires an authenticated deterministic checkpoint.
Task 5: minor (compatibility): legacy FROZEN snapshots containing any `online_state` sentinel must be regenerated because frozen mode now rejects the key's presence fail-closed.
Task 5: exceptional fix round 6 (1 addressed, 0 open; commits 1286860..16ce12f)
Task 5: complete (commits 418f6ab..16ce12f, review clean)
Task 6: review found 1 critical and 6 important issues: undeclared seed operands/evaluator columns bypass the sealed grammar boundary; search is shuffled enumeration rather than evolutionary; default fitness bypasses the Task 4 execution engine/cost model; nested idempotent Boolean canonicalization corrupts semantic identity; resume does not authenticate the full persisted result/development evidence; seed-rule order changes bounded-search results; and outcomes available exactly at decision time are accepted.
Task 6: fix round 1/5 in progress (7 open; implementation commit 8e17672).
Task 6: durable outer-block consumption is assigned to Task 7's atomic pipeline/persistence boundary; Task 6's pure promotion helper is not the production consumption boundary.
Task 6: fix round 1/5 (6 addressed, 1 open — Task 4 execution bars after the sealed boundary can still change learning fitness; commits 8e17672..5581ef4).
Task 6: fix round 2/5 (1 addressed, 0 open; commits 5581ef4..0cee3e4).
Task 6: complete (commits 16ce12f..0cee3e4, review clean).
Task 7: Ruling: expose the new scoped workflow under a typed `strategy` CLI namespace (`ingest`, `evaluate`, `learn`, `export`) while preserving legacy earnings commands and the existing snapshot-export alias — this gives Task 8 stable argument construction without breaking current users — cost if wrong: Task 8 invocation fixtures and CLI help text will need adjustment.
Task 7: Ruling: atomically consume each inspected learned-rule forward block through a deterministic unique `causal_audits` record before returning a promotion decision — inspection itself spends the block, including a rejected promotion — cost if wrong: the persistence contract becomes more conservative and repeat research decisions require a genuinely new forward period.
Task 7: review found 7 important issues: live-provider retrieval timestamps collapse bar decisions; learning/evaluation select inconsistent post-filter final boundaries; Task 5 ensemble orchestration is absent; learning DTO/boundary persistence is incompatible with Task 8; forced evaluation IDs can race; evaluation persistence is non-atomic; and incomplete/unavailable histories surface as success.
Task 7: fix round 1/5 in progress (7 open; implementation commit 69347c8).
Task 7: fix round 1/5 implemented (7 addressed, pending fresh re-review; commits 69347c8..db73982).
Task 7: re-review found 7 important open issues: corrected bars collapse the original causal revision path; provider correction timestamps precede HTTP receipt; scalar caches can falsely satisfy plural ensemble requests; configured strategy/family caps are ignored; forced runs lack run-scoped child evidence; failed fetches do not persist incomplete coverage; and wall-clock coverage treats legitimate Alpaca closures as gaps.
Task 7: fix round 2/5 in progress (7 open; fix-round-1 commit db73982).
Task 7: fix round 2/5 implemented (7 addressed, pending fresh re-review; commits db73982..5bc6224).
Task 7: re-review 2 found 5 important open issues: revision-aware feedback is aligned to bars by ordinal position; plural cohort reservations can interleave; requested-coverage reservations can collide; the offline XNYS schedule mis-models Alpaca hourly/daily aggregation and early closes; and snapshot cohort selection can mix components after a pre-group limit.
Task 7: fix round 3/5 in progress (5 open; fix-round-2 commit 5bc6224).
Task 7: fix round 3/5 implemented (5 addressed, pending fresh re-review; commits 5bc6224..bae60e7).
Task 7: re-review 3 found 4 important open issues: feedback execution mapping still bypasses Task 4 actionability; overlapping scalar/plural cohorts can race on shared component keys; XNYS incorrectly observes a Saturday New Year on the preceding Friday; and equal-time complete snapshot cohorts lack a deterministic total order.
Task 7: fix round 4/5 in progress (4 open; fix-round-3 commit bae60e7).
Task 7: fix round 4/5 implemented (4 addressed, pending fresh re-review; commits bae60e7..51102ac).
Task 7: re-review 4 found 3 important open issues: valid no-fill/abstention outcomes produce a columnless frame and fail; an ambiguous post-commit exception can downgrade evaluated runs to failed; and stale calendar-version coverage is accepted/exported under a newer runtime calendar.
Task 7: fix round 5/5 in progress (3 open; fix-round-4 commit 51102ac).
Task 7: fix round 5/5 implemented (3 addressed, pending final fresh re-review; commits 51102ac..b3c92cd).
Task 7: final re-review found 2 important linked issues: only the newest coverage request is authenticated even when evaluation consumes multiple ranges, and snapshots fall back to a single request rather than the aggregate manifest used by a terminal evaluation.
Task 7: Ruling: run one exceptional user-authorized sixth fix cycle to authenticate the complete contributing coverage set and persist/export the exact aggregate evaluation manifest — the user's explicit fully autonomous authorization permits this decision, and stopping would leave snapshot provenance inconsistent with evaluated history — cost if wrong: one extra implementation/review cycle and stricter re-ingestion requirements for older contributing ranges.
Task 7: exceptional fix round 6 in progress (2 open; fix-round-5 commit b3c92cd).
Task 7: exceptional fix round 6 implemented (2 addressed, pending final fresh re-review; commits b3c92cd..33aaa0f).
Task 7: exceptional re-review found 3 important issues: coverage authentication and revision-ledger reads have a TOCTOU race; rejected stale aggregate manifests can fall through to unrelated legacy coverage; and snapshot coverage deduplication/bounds differ from the exported projection while raw ensemble evidence retains unbounded contributors.
Task 7: Ruling: run one final narrowly scoped exceptional hardening cycle under the user's explicit fully autonomous authorization — all three findings are direct consequences of the aggregate-manifest boundary and deferring them would invalidate the no-repaint and bounded-snapshot contracts — cost if wrong: another implementation/review cycle and serialization of evaluation against same-instrument ingestion.
Task 7: exceptional fix round 7 in progress (3 open; exceptional-round-6 commit 33aaa0f).
Task 7: exceptional fix round 7 implemented (3 addressed, pending final fresh re-review; commits 33aaa0f..4f7c7ef).
Task 7: final scoped re-review found 2 snapshot-validation issues: malformed gap entries can be filtered into a false complete projection, and non-object coverage manifests can pass through raw/unbounded in ensemble evidence.
Task 7: exceptional validation micro-fix round 8 in progress (2 open; exceptional-round-7 commit 4f7c7ef).
Task 7: exceptional validation micro-fix round 8 implemented (2 addressed, pending final scoped re-review; commits 4f7c7ef..bfd82a8).
Task 7: exceptional validation micro-fix round 8 (2 addressed, 0 open; commits 4f7c7ef..bfd82a8).
Task 7: complete (commits 0cee3e4..bfd82a8, review clean).
Task 8: Ruling: present the sign of the current ensemble contribution as a clearly labelled research posture (Long contribution, Short contribution, or Abstain), not as an executable trade instruction — schema v2 does not claim a broker-ready order and the UI must not manufacture one — cost if wrong: Task 7 snapshot v2 will need a separately authenticated current-decision DTO and Task 8 presentation fixtures will change.
Task 8: implementation complete pending fresh review (commit cadf906; 38 Swift tests, 16 Python compatibility tests, release/app build and visual QA clean).
Task 8: review found 6 important issues: strategy/evidence joins and plural actions omit exact dataset/cohort/mode context; learning/budget/CSV request validation diverges from Task 7; v2 instants/evidence decoding is too permissive/unbounded; early cancellation can launch an orphan process; streamed progress does not drive learning state and evaluate/learn reload an unchanged snapshot; and engine/stale-snapshot failures are not durably visible.
Task 8: fix round 1/5 in progress (6 open; implementation commit cadf906).
Task 8: fix round 1/5 implemented (6 addressed, pending fresh re-review; commits cadf906..991c399).
Task 8: re-review found 3 important open issues: automatic export drops the strategy asset's custom database URL; stale-banner Refresh can rebuild the default snapshot but reload an unchanged custom snapshot path; and the reported stale light-wide capture was actually a duplicate narrow capture because the script did not assert window dimensions.
Task 8: fix round 2/5 in progress (3 open; fix-round-1 commit 991c399).
Task 8: fix round 2/5 implemented (3 addressed, pending fresh re-review; commits 991c399..383a3ee).
Task 8: fix round 2/5 (3 addressed, 0 open; commits 991c399..383a3ee).
Task 8: complete (commits bfd82a8..383a3ee, review clean; 53 Swift tests, 464 Python tests, release/app build, smoke and visual QA clean).
Task 9: Ruling: exclude `docs/superpowers/plans` from Ruff formatting instead of committing formatter-only edits to the approved execution plan — the plan is immutable coordination metadata, while product Python and checked documentation remain linted — cost if wrong: CI will not enforce Python-snippet formatting inside plan Markdown and the exclusion must be removed with the plan reformatted.
Task 9: review found 1 critical, 3 important, and 2 minor issues: live research can reuse unscoped database state; exhaustive Binance history was not completed; execution-assumption provenance disagrees with effective lot size; provider credentials can evade secret scanning; ensemble policy is hardcoded; and the end-to-end test does not inject a failed strategy.
Task 9: minor (deferred): published ensemble-policy values are hardcoded instead of derived from the pipeline's `EnsembleConfig`.
Task 9: minor (deferred): the end-to-end test covers unavailable strategies but not a deliberately failed strategy, though lower-level weighting tests cover failure exclusion.
Task 9: fix round 1/5 in progress (4 open; implementation commit ecc324d).
Task 9: fix round 1/5 implemented (4 addressed plus 2 deferred minors addressed, pending scoped re-review; commits ecc324d..cfcf948).
Task 9: fix round 1/5 (6 addressed, 0 original findings open; commits ecc324d..cfcf948); scoped re-review found 1 new important issue: surviving strategies are evaluated as separate single-member cohorts after a generator failure instead of one coherent survivor ensemble.
Task 9: fix round 2/5 in progress (1 open; fix-round-1 commit cfcf948).
Task 9: fix round 2/5 (1 addressed, 0 open; commits cfcf948..831204b).
Task 9: complete (commits 383a3ee..831204b, review clean; 477 Python tests, 53 Swift tests, release build, schema drift, secret scan, exhaustive provider evidence and deterministic cache replay clean).
Task 10: in progress (whole-product verification and publication).
Task 10: whole-branch review found 1 critical, 6 important, and 2 minor issues: learning provenance can be retrodated across the sealed final boundary; fold/final returns are shifted one bar; equity-session strategies use a continuous UTC calendar; REST backfills overclaim original-vintage revision fidelity; required robustness diagnostics are not promotion gates; ensemble probability/economic gates are placeholders; CI does not assert Python-to-Swift fixture parity; cancellation lacks kill escalation; and the secret scanner ignores Git history.
Task 10: final fix round 1/1 in progress (9 open; review head 25d6e15).
Task 10: final fix round 1/1 (5 addressed, 4 residual; commits 25d6e15..f4f1dd3): decision/outcome mapping, XNYS sessions, REST vintage fail-closed handling, cancellation escalation, and Git-history secret scanning are clean; learning event timestamps, robustness receipts, missing-cost fail-closed behavior, and fixture parity remain open.
Task 10: Ruling: treat synthetic run-start-plus-ordinal learning timestamps as a load-bearing causal-provenance defect and halt publication — the final re-review confirmed candidate discovery/evaluation events are not individually clock-stamped, so forward-promotion evidence can be misclassified — cost if wrong: an exceptional second final-review fix wave and another exhaustive verification cycle delay release.
Task 10: Ruling: treat unreceipted robustness aggregates as a load-bearing promotion defect and halt publication — PBO, median edge, calibration, and neighborhood stability must carry sealed-boundary provenance rather than only aggregate values — cost if wrong: additional schema/fixture churn and conservative strategies remain unavailable longer.
Task 10: Ruling: treat absent economic-cost evidence defaulting to zero as a load-bearing decision-gate defect and halt publication — missing costs must fail closed or an apparently calibrated posture can pass an optimistic economic gate — cost if wrong: another validation/ensemble regression cycle and fewer actionable research postures until real cost evidence exists.
Task 10: Ruling: treat self-referential Swift fixture synchronization as a load-bearing CI defect and halt publication — parity must compare generated Python research semantics with the Swift fixture, not recycle the Swift fixture as its own research source — cost if wrong: an exceptional second fix wave and regenerated fixture hashes before merge.
Task 10: publication blocked at the final-review breaker pending explicit user direction; no merge or push performed.
