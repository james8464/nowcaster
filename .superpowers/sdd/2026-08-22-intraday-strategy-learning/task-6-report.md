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

- `LearningExperiment` binds identifiers, UTC chronology, sealed boundary, seed, fixed budget, inner folds, indicator/parameter grid, grammar caps, cost assumptions, fitness penalties, evaluator identity, and explicit evaluator version.
- A canonical full search-contract hash is stored in every trial payload and verified on resume.
- Candidate ordering is deterministic across process input/configuration order for a fixed seed.
- Search evaluates exactly the fixed ledger budget. Candidate-space exhaustion produces deterministic `budget_stop` rows rather than silent early stopping.
- Invalid cap-breaking candidates, evaluator failures, successful candidates, and budget stops all become append-only ledger rows. `LearningResult.trial_count` is exactly `len(trials)` and matches durable ledger count.
- Fitness is median inner-fold validation net Sharpe less median absolute drawdown, median turnover, fold Sharpe population instability, and node-count MDL/complexity penalties.
- The default evaluator applies explicit transaction costs to validation returns; injected evaluators must return typed net/cost-aware `FoldMetrics` and are version-bound for resume.

### Leakage and chronology boundaries

- Every timestamp supplied to learning or promotion must be explicit UTC.
- Every learner input row must be finalized and available by its decision time and by experiment `as_of`.
- Development outcomes must become available after their decision and by experiment `as_of`.
- Any row at or beyond `sealed_final_start`, or any column named as sealed/final evidence, fails closed.
- Inner folds are non-empty, in range, unique, strictly chronological, and require training outcomes to be available by validation start.
- Fold validation occurs before the first candidate query, so malformed evidence is not misclassified as a failed trial.
- Evaluators receive deep copies of training and validation slices only.

### Persistence, versioning, and promotion

- Trials use deterministic IDs and evaluation timestamps and are inserted, never upserted or updated.
- Resume accepts only a contiguous deterministic ledger prefix with matching context, full search-contract hash, candidate generation, version, identity, status, fold metrics, and recomputed fitness.
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

## Verification

- Task 6 focused: `pytest tests/unit/test_learning_grammar.py tests/unit/test_learning_search.py tests/integration/test_learning_mode.py -q` -> **26 passed in 1.22s**.
- Relevant Task 3-5 compatibility: indicators, strategy library, no-repaint, execution engine, intraday backtest, strategy validation, ensemble, strategy engine integration, and schema integration -> **196 passed in 14.39s**.
- Fresh full Python suite after final executable change: `pytest` -> **381 passed in 83.82s**.
- Changed-file Ruff: `ruff check ...` -> **All checks passed**.
- Final staged diff check: `git diff --cached --check` -> **passed with no output**.

## Final self-review

- **Final leakage:** sealed/final columns and boundary rows reject before candidate generation; evaluator slices contain inner development folds only; promotion accepts new forward evidence, never the sealed search block.
- **Determinism across process/order:** indicator and threshold inputs are normalized, semantic candidates are canonicalized/deduped, seeded order is fixed, trial IDs/timestamps and candidate versions are content-derived, and ties break on full candidate hash.
- **Resume idempotency:** persisted trials form a contiguous prefix and bind the full context/search/evaluator version; completed queries are not reevaluated; discovery insertion is deterministic and idempotent.
- **Failed-trial persistence:** failures, invalid cap attempts, and budget stops insert immediately and remain part of the actual trial count on resume.
- **Version collisions:** full candidate hashes identify semantics; run-derived immutable versions distinguish discovery runs; deterministic rule IDs distinguish persisted discoveries.
- **Mutation/cap bypass:** all candidate rules are cap-checked before an evaluator call; invalid seeds are ledgered as invalid; no active candidate object is mutated; promotion rejects active/retired rules.
- **No repaint:** grammar operations use only current/past values and explicit lags; future-row append invariance is tested.

## Concerns

- The machine-global Anaconda `pytest` launcher is not usable for this repository; the checked-in project workflow already uses `.venv`, which is green.
- Callers supplying a custom evaluator must increment `evaluator_version` whenever behavior or external assumptions change. Resume fails closed when the explicit version changes.
- Durable marking of an inspected outer block as consumed is a pipeline/persistence responsibility beyond this pure Task 6 promotion function; Task 6 rejects authenticated consumed evidence and never mutates evidence locally.
