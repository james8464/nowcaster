# Task 6 Report: Interpretable Learning Mode

## Outcome

Implemented an immutable typed rule grammar, deterministic bounded structure/parameter search, append-only learning trial ledger with authenticated resume, immutable run-versioned shadow discoveries, and pure forward-evidence promotion gates.

The learner rejects non-UTC, unfinalized, not-yet-available, sealed/final, and chronologically malformed evidence before candidate evaluation. Search uses only declared inner chronological folds. Final rows and columns never enter candidate generation, evaluator inputs, fitness, stopping, discovery, or promotion.

## Files

- Created `src/learning/__init__.py`
- Created `src/learning/grammar.py`
- Created `src/learning/search.py`
- Created `src/learning/promotion.py`
- Created `tests/unit/test_learning_grammar.py`
- Created `tests/unit/test_learning_search.py`
- Created `tests/integration/test_learning_mode.py`
- Created this report

No shared Task 1-5 production interface or schema file was changed. Task 1's existing `learning_trials` and `discovered_rules` tables are consumed as-is.

## Implemented behavior

### Typed bounded grammar

- Closed operators only: lagged indicators, finite numeric constants, typed comparisons, crossovers, `AND`, `OR`, and `NOT`; no arbitrary Python or `eval` path.
- Immutable `RuleNode` validation rejects type mismatches and malformed terminals.
- Maximum depth and node caps are enforced before evaluation.
- Canonical serialization normalizes inverse comparisons, inverse crossovers, commutative Boolean ordering, associativity, and idempotent duplicates.
- Semantic hashes drive deterministic deduplication.
- Seeded mutation is independent of caller ordering and remains within configured lag/depth/node bounds.
- Plain-language rendering and pandas evaluation are prefix invariant.

### Search and fitness

- `LearningExperiment` binds identifiers, UTC chronology, sealed boundary, seed, fixed budget, inner folds, indicator/parameter grid, grammar caps, every typed Task 4 execution/risk assumption, fitness penalties, evaluator identity/version, and an explicit net-cost-aware evaluator contract.
- A canonical full search-contract hash and validated-development-frame digest are stored in every authenticated trial receipt and verified on resume.
- Seed rules are canonically deduplicated/sorted. Candidate ordering is deterministic across row/configuration order and fresh processes for a fixed seed.
- Search starts with a bounded seeded population, then uses validation fitness to rank parents and deterministically generate bounded parameter/operator/lag mutations and Boolean crossovers. Later candidates change when early parent fitness changes.
- Search evaluates exactly the fixed ledger budget. Candidate-space exhaustion produces deterministic `budget_stop` rows rather than silent early stopping.
- Invalid cap-breaking candidates, evaluator failures, successful candidates, and budget stops all become append-only ledger rows. `LearningResult.trial_count` is exactly `len(trials)` and matches durable ledger count.
- Fitness is median inner-fold validation net Sharpe less median absolute drawdown, median turnover, fold Sharpe population instability, and node-count MDL/complexity penalties.
- The default evaluator converts Boolean rules to a versioned long/short signal contract and routes them through Task 4's event-driven intraday backtest, preserving next-actionable-bar, latency, collision, spread/slippage/fees, funding/borrow, and risk behavior. Fold metrics are computed only over the validation execution horizon; prepended causal warmup rows do not dilute them.
- Injected evaluators receive only deterministic allowlisted finalized timestamps, declared indicators, and an explicitly declared return column when present. They cannot observe undeclared labels or execution columns and must return typed net/cost-aware `FoldMetrics` under a versioned cost contract.

### Leakage and chronology boundaries

- Every timestamp supplied to learning or promotion must be explicit UTC.
- Every learner input row must be finalized and available by its decision time and by experiment `as_of`.
- Development outcomes must become available strictly after their decision and by experiment `as_of`; equality fails closed.
- Any row at or beyond `sealed_final_start`, or any column named as sealed/final evidence, fails closed.
- Inner folds are non-empty, in range, unique, strictly chronological, and require training outcomes to be available by validation start.
- Fold validation occurs before the first candidate query, so malformed evidence is not misclassified as a failed trial.
- Every seed/generated terminal is recursively checked against the declared indicator, lag, parameter, threshold, depth, and node domains before evaluator invocation. Invalid selected queries are ledgered and never reach the evaluator.
- Evaluators receive deep copies of strict typed allowlisted training and validation slices only. The Task 4 engine receives only execution-bar columns and never undeclared labels/indicators.

### Persistence, versioning, and promotion

- Trials use deterministic IDs and evaluation timestamps and are inserted, never upserted or updated.
- Every ledger payload contains an immutable canonical receipt over the complete row/result contract, deterministic timestamps/source/version, candidate payload, fold count/metrics/fitness/status/error semantics, experiment hash, and validated-frame digest.
- Resume accepts only a contiguous deterministic ledger prefix, regenerates each evolutionary candidate from authenticated preceding fitness, recomputes fitness, and rejects any changed row, payload, source/version, timestamp, status/error, claimed dataset/frame, or candidate identity.
- Failed, invalid, and budget-stop rows survive resume and count toward the budget.
- The best successful rule is persisted idempotently as a new immutable `shadow` candidate.
- Candidate versions include the immutable rule definition and learning-run identity, so later discovery runs cannot mutate or collide with an active version.
- `promote_candidate` is pure: it never mutates the candidate or evidence.
- Promotion requires matching candidate identity/version, a genuinely later forward period, an inspected and caller-authenticated unconsumed outer block, a passed causal audit, and the normal Task 5 `PromotionDecision` gates.
- Active or retired rules cannot be mutated by learning mode. Durable outer-block consumption is intentionally owned by the later pipeline/persistence boundary; this pure function fails closed when supplied `outer_block_consumed=True`.

## TDD evidence

All commands below were run after activating the repository `.venv` so the command text from the brief used the supported Python 3.12 environment.

### Baseline

- Ambient `pytest` was `/opt/anaconda3/bin/pytest` on unsupported Python 3.14 and failed collection because it could not import `src` and lacked `respx`. No code had been changed.
- Repository baseline: `.venv/bin/pytest` -> **355 passed in 81.87s**.

### Prescribed grammar RED/GREEN

- RED command: `pytest tests/unit/test_learning_grammar.py -v`
- RED result: exit 2, expected `ModuleNotFoundError: No module named 'src.learning'`.
- First GREEN attempt exposed two real behavior mismatches (numeric type error wording and grouping of `NOT` rendering): 5 passed, 2 failed.
- GREEN command: `pytest tests/unit/test_learning_grammar.py -v`
- GREEN result: **7 passed in 0.06s**.
- Later semantic hardening RED proved inverse crossover/idempotent Boolean canonicalization was missing; GREEN now includes **8 grammar tests**.

### Prescribed search RED/GREEN

- RED command: `pytest tests/unit/test_learning_search.py -v`
- RED result: exit 2, expected `ModuleNotFoundError: No module named 'src.learning.search'`.
- First GREEN attempt exposed validation-order leakage reporting: 6 passed, 1 failed.
- GREEN command: `pytest tests/unit/test_learning_search.py -v`
- GREEN result: **7 passed in 0.71s**.
- Later RED/GREEN hardening added malformed-fold preflight and per-decision availability. The final file contains **9 search tests**.

### Integration RED/GREEN

- RED command: `pytest tests/integration/test_learning_mode.py -v`
- RED result: exit 2, expected `ModuleNotFoundError: No module named 'src.learning.promotion'`.
- First GREEN attempt: 4 passed, 1 failed because DuckDB's `Float` round-trip changed the exact persisted fitness representation. Resume was corrected to recompute and authenticate fitness from typed fold evidence.
- GREEN result: **5 passed in 1.00s**.
- Ledger audit RED then produced exactly three expected failures: invalid rules were rejected before ledgering, candidate-space exhaustion raised instead of ledgering, and resume authenticated only a partial context.
- Ledger audit GREEN: **8 passed in 1.28s**, proving durable `invalid`, `failed`, and `budget_stop` semantics plus full search-contract resume binding.
- Review RED proved a process-local hidden evidence-consumption mutation made promotion impure. It was removed; GREEN now proves repeat calls are deterministic and caller-supplied consumed evidence rejects.
- Evaluator-version RED failed because no explicit version token existed. GREEN binds it into the authenticated resume contract.
- The final integration file contains **9 tests**.

### Review fix round 1/5 RED/GREEN

- Nested Boolean RED: `.venv/bin/pytest tests/unit/test_learning_grammar.py -v` -> **2 failed, 8 passed** (`AND` and `OR` nested associativity/idempotence). GREEN -> **10 passed**.
- Domain/evidence/order/time RED: `.venv/bin/pytest tests/unit/test_learning_search.py -v` -> **7 failed, 9 passed** (four escaped seed-domain cases, evaluator allowlist, seed order, equal outcome time). GREEN -> **16 passed**.
- Evolution RED: `.venv/bin/pytest tests/unit/test_learning_search.py::test_inner_fitness_selects_parents_and_changes_later_candidates -v` -> **1 failed** because the fixed shuffled pool ignored fitness. GREEN -> **1 passed**; the full search unit file then passed **17 tests**.
- Task 4 RED: `.venv/bin/pytest tests/unit/test_learning_search.py::test_default_fitness_uses_versioned_task4_execution_and_cost_assumptions -v` -> **1 failed** because `LearningExperiment` had no typed execution contract and used flat forward-return arithmetic. GREEN -> **1 passed**, covering spread, latency, funding, short borrow, and doubled costs.
- Validation-horizon RED: `.venv/bin/pytest tests/unit/test_learning_search.py::test_default_fold_metrics_exclude_prepended_training_only_returns -v` -> **1 failed** with different Sharpes (`-5.612486...` versus `-6.480740...`). GREEN -> **1 passed** after limiting causal warmup and recomputing Task 4 metrics strictly on the validation execution horizon.
- Receipt/evolutionary-resume RED: `.venv/bin/pytest tests/integration/test_learning_mode.py -v` -> **7 failed, 8 passed**: evolutionary ordinal 4 could not resume; status/error/timestamp/source/source-version corruption was accepted; the changed-frame test initially exposed a pandas fixture dtype issue, then correctly failed because the same claimed dataset hash accepted different evidence. GREEN -> **15 passed**, later expanded with candidate-payload corruption and evaluator cost-contract coverage.

### Review findings addressed

1. Critical domain/evidence bypass: recursive terminal validation plus strict evaluator allowlist; unknown/future indicators, lag 99, arbitrary indicator parameters, off-grid thresholds, and undeclared future labels are covered.
2. Deterministic evolution: fitness-ranked seeded parents now drive bounded mutation/crossover; fresh-process/order determinism, changed-parent fitness, fixed budgets, semantic dedupe, and resume are covered.
3. Task 4 fitness: default evaluation uses the event-driven backtest; the full execution/risk and directional-signal contract is hashed; validation-only metrics and individual cost/latency/carry sensitivities are covered.
4. Nested canonicalization: normalized child operators drive safe `AND`/`OR` flattening, preserving associativity and idempotence.
5. Resume receipt: schema-v2 receipts authenticate the complete row/result and actual validated frame, including deterministic generation and result semantics.
6. Seed normalization: semantic duplicates are deterministically represented, deduplicated, and sorted before hashing/generation.
7. Outcome availability: `outcome_available_at <= decision_timestamp` is rejected.

### Review fix round 2/5: execution chronology boundary

- The remaining critical probe showed that the feature decision chronology was bounded but Task 4 `open_timestamp`/`close_timestamp` values were not. A last inner-validation decision could therefore borrow a post-`as_of` or post-seal execution bar and change fitness.
- RED command: `.venv/bin/pytest tests/unit/test_learning_search.py::test_post_seal_execution_rows_are_rejected_before_search tests/unit/test_learning_search.py::test_execution_row_after_experiment_as_of_is_rejected_before_search tests/unit/test_learning_search.py::test_finalized_execution_close_must_precede_availability_and_decision -v`.
- RED result: **5 failed**. Both post-seal close variants, the post-`as_of` execution row, close after decision/availability, and availability before close all incorrectly completed without raising.
- GREEN result for the same command: **5 passed in 0.58s**.
- Every Task 4 row is now preflighted before fold construction/candidate querying: all execution timestamps must be explicit UTC; `open_timestamp < close_timestamp <= available_at <= decision_timestamp`; open/close/availability/decision must be no later than experiment `as_of` and strictly before `sealed_final_start`; bars must be finalized, numeric, and symbol-consistent. Existing decision/outcome/as-of invariants remain in force.
- `_default_evaluator` receives only this validated development frame. If no legal next actionable bar exists inside it, Task 4 deterministically rejects/abstains; post-boundary rows fail the whole evidence contract and are never projected into fitness.

## Verification

- Task 6 focused: `.venv/bin/pytest tests/unit/test_learning_grammar.py tests/unit/test_learning_search.py tests/integration/test_learning_mode.py -q` -> **51 passed in 3.22s**.
- Relevant Task 3-5 compatibility: indicators, strategy library, no-repaint, execution engine, intraday backtest, strategy validation, ensemble, strategy engine integration, and schema integration -> **196 passed in 14.98s**.
- Fresh full Python suite after final executable change: `.venv/bin/pytest -q` -> **406 passed in 83.24s**.
- Changed-file Ruff: `.venv/bin/ruff check src/learning/grammar.py src/learning/search.py tests/unit/test_learning_grammar.py tests/unit/test_learning_search.py tests/integration/test_learning_mode.py` -> **All checks passed**.
- Working-tree and staged diff checks: `git diff --check` and `git diff --cached --check` -> **passed with no output**.

## Final self-review

- **Final leakage:** sealed/final columns and boundary rows reject before candidate generation; custom evaluators receive a strict allowlist; default execution receives only Task 4 bar fields; candidate features stop at each inner validation block; promotion accepts new forward evidence, never the sealed search block.
- **Determinism across process/order:** indicator, threshold, and seed inputs are normalized; semantic candidates are canonicalized/deduped; seeded evolution is content-derived; fresh-process output is tested; ties break on the full candidate hash.
- **Resume idempotency:** canonical receipts bind the actual validated frame, full experiment/evaluator/cost context, result semantics, and deterministic evolutionary history; completed queries are not reevaluated; discovery insertion is deterministic and idempotent.
- **Failed-trial persistence:** failures, invalid domain/cap attempts, and budget stops insert immediately and remain part of the actual trial count on resume. Candidate-space enumeration is not counted as evaluation; every selected candidate/parameter query is one deterministic ledger row, including pre-evaluator invalid selections, evaluator failures, and exhaustion stops.
- **Version collisions:** full candidate hashes identify semantics; run-derived immutable versions distinguish discovery runs; deterministic rule IDs distinguish persisted discoveries.
- **Mutation/cap bypass:** every terminal is recursively domain-checked and every rule cap-checked before an evaluator call; invalid seeds are ledgered as invalid; generated mutations/crossovers are bounded and deduplicated; no active candidate object is mutated; promotion rejects active/retired rules.
- **No repaint:** grammar operations use only current/past values and explicit lags; future-row append invariance is tested.

## Concerns

- The machine-global Anaconda `pytest` launcher is not usable for this repository; the checked-in project workflow already uses `.venv`, which is green.
- Callers supplying a custom evaluator must maintain truthful `evaluator_version` and `evaluator_cost_contract` values whenever behavior or external assumptions change. Both and the typed Task 4 execution/risk assumptions are receipt-bound; Task 6 cannot independently prove the internals of trusted injected code.
- Durable marking of an inspected outer block as consumed is a pipeline/persistence responsibility beyond this pure Task 6 promotion function; Task 6 rejects authenticated consumed evidence and never mutates evidence locally.
