# Deep Research Reliability Design

## Purpose

Nowcaster will add a one-click Deep Research mode that repeatedly searches for, simulates, rejects, and compares day-trading strategies for one selected asset and interval. The feature is intended to improve the quality of research evidence and the probability of finding a durable net edge. It must not claim that profitability is guaranteed or that historical simulation is equivalent to live performance.

The system's definition of reliability is operational: evidence is reproducible, point-in-time, non-repainting, net of conservative costs, stable across unseen periods and plausible stresses, and independently confirmed in forward operation. When that standard is not met, the correct output is **No reliable strategy found** and the correct trading decision is abstention.

## Non-negotiable constraints

1. Deep Research never submits broker orders and cannot arm live trading.
2. Search, calibration, and ensemble construction never observe the sealed final-test partition.
3. Every attempted candidate counts in the immutable trial ledger, including invalid, failed, duplicated, interrupted, and unprofitable candidates.
4. A modified candidate receives a new version and a new forward-evidence cohort.
5. Research, shadow, paper, and live evidence remain visibly separate.
6. Earlier features, signals, orders, and fills must be prefix invariant when future data is appended or changed.
7. Missing, incompatible, or unauthenticated data is reported as unavailable; it is never imputed and presented as provider evidence.
8. The application may use all currently active processors in Performance mode, but it must yield to critical thermal state, low-power policy, user cancellation, memory pressure, and operating-system termination.
9. No generated rule may execute arbitrary code, access the network, read credentials, or mutate configuration.
10. A profitable backtest alone can never unlock live execution.

## Meaning of “all available data”

For a selected asset/feed/interval, the engine uses every compatible observation that can be authenticated from:

- the configured market-data provider;
- previously cached provider responses whose hashes and provenance validate;
- strict user CSV imports; and
- existing point-in-time alternative, macro, earnings, and fundamental fields whose publication time precedes the decision time.

The ingestion manifest records requested and received date ranges, provider, feed, venue, symbol, interval, adjustment policy, timezone, row count, first and last timestamps, gaps, duplicates, corporate-action handling, and content hash. Provider feeds are never silently spliced. Consolidated and venue-limited data remain separate datasets. Dataset completeness is a hard input to eligibility and is displayed in the app.

“All available” does not mean inaccessible proprietary feeds, future observations, revised values whose original publication state is unknown, or synthetic values inserted into real-data coverage. If provider history has a retention limit, the exact limitation is part of the result.

## Architecture

Deep Research extends the existing ingestion, strategy, learning, backtest, snapshot, and execution boundaries instead of creating a second research stack.

### Research coordinator

A `DeepResearchCoordinator` owns a versioned run. It performs these stages:

1. Resolve and validate the complete dataset snapshot.
2. Pre-register the search space, random seeds, trial budget, evaluation protocol, cost policy, and promotion thresholds.
3. Build chronological development, nested walk-forward, and sealed final-test boundaries before generating candidates.
4. Dispatch pure candidate evaluations to isolated worker processes.
5. Commit results through one coordinator-owned database writer in deterministic ordinal order.
6. Generate new challengers from development-only evidence.
7. Stress-test eligible finalists.
8. Consume the sealed final test once for a frozen candidate version.
9. Publish a champion comparison and research-only promotion decision.
10. Checkpoint the run and export a new app snapshot.

The coordinator can resume only when its dataset hash, code hash, protocol hash, and search-space hash match. Otherwise it starts a new run. A stopped run remains in the trial ledger.

### Parallel compute model

Candidate evaluation is CPU-bound and uses a process pool rather than Swift tasks or Python threads. Workers receive immutable serialized candidate and fold descriptions and return metrics; they do not write SQLite or modify shared data.

The native app offers:

- **Balanced:** at most `max(1, activeProcessorCount - 1)` workers.
- **Performance:** all `activeProcessorCount` workers.
- **Custom:** 1 through `activeProcessorCount` workers.

Each worker sets numeric-library thread counts to one so process-level parallelism does not oversubscribe the machine. The coordinator bounds queued work, estimates resident memory before dispatch, checkpoints completed trials, and retries a crashed trial once before recording failure. Serious thermal state reduces concurrency to one; critical state pauses dispatch until recovery. Low Power Mode defaults to Balanced. User pause stops new dispatch and drains active work; Stop terminates active workers after checkpointing completed results.

### Closed candidate language

Self-improvement operates only inside typed, bounded search spaces:

- causal indicator-rule trees from the existing grammar;
- registered strategy parameter schemas;
- long, short, and abstention thresholds;
- volatility, liquidity, time-of-day, and regime filters;
- stop, target, holding-period, and sizing parameters within hard research bounds;
- non-negative ensemble weights subject to strategy and family caps; and
- registered deterministic model families that implement the existing fit/predict protocol.

Search combines seeded baselines, stratified parameter sampling, evolutionary mutation/crossover, and successive-halving allocation. Duplicate semantic hashes are recorded but not re-evaluated. Complexity and turnover penalties favor simpler candidates. The learner cannot synthesize Python, shell commands, SQL, broker requests, or unrestricted expressions.

### Champion–challenger cycles

Each cycle starts from the prior champion and a diverse baseline population. Challengers are compared against cash, buy-and-hold where applicable, simple equal-weight strategy ensembles, and the prior champion. A challenger must pass every hard gate before its development evidence score is considered. A champion is replaced only by a challenger whose conservative score improvement exceeds the minimum materiality margin and whose confidence interval is not explained by one fold or one regime.

Repeated cycles do not repeatedly inspect the same final test. A final-test result is permanently associated with one frozen candidate version. Further learning requires newly arrived, outcome-observable data or a newly pre-registered outer block, and creates a new version and cohort.

## Evaluation protocol

### Chronology and leakage controls

The final segment is selected from the full chronology before filtering observations. Development uses expanding nested walk-forward folds. Training labels that overlap validation are purged, and embargo length covers the forecast horizon, maximum feature publication delay, and execution latency. Fitted transforms, calibrators, regime models, thresholds, and ensemble weights are trained separately inside each fold.

Every finalist must pass automated prefix-invariance tests on features, component signals, ensemble weights, orders, and fills. A causal failure invalidates the candidate and all descendants derived from it.

### Execution model

Signals execute no earlier than the next actionable bar. Simulation includes:

- explicit commission and fee schedules;
- observed or conservative spread estimates;
- size-dependent slippage and market impact;
- signal-to-order latency;
- volume participation limits and partial fills;
- rejected and expired orders;
- exchange sessions, auctions, halts, and gaps;
- short borrow availability and borrow fees;
- cryptocurrency funding where applicable;
- stop-before-target ordering when a bar touches both; and
- forced liquidation and unavailable-liquidity scenarios.

Where required microstructure inputs are absent, conservative defaults are applied and labelled. A result cannot receive the highest evidence grade without observed spread/liquidity inputs appropriate to its intended execution venue.

### Stress and future-path simulation

Eligible candidates undergo deterministic stress matrices and seeded Monte Carlo analysis. Future-path simulation uses stationary/block bootstrap sampling that preserves short-range dependence and volatility clustering; it does not invent a single forecast and call it the future.

Required scenarios include baseline costs, doubled costs, severe costs, delayed fills, reduced liquidity, skipped best trades, clustered losses, regime-specific blocks, parameter neighbors, alternate start dates, and trade-order resampling. Stress results affect eligibility, never inflate headline expected return.

### Trial-aware statistics

The immutable ledger supplies the actual number and dependence structure of trials. The evaluation publishes:

- median and dispersion of walk-forward net Sharpe;
- net return, maximum drawdown, expected shortfall, turnover, trade count, and exposure;
- probabilistic and Deflated Sharpe ratios;
- probability of backtest overfitting from combinatorially symmetric validation;
- bootstrap probability that net edge is positive;
- multiple-testing-adjusted significance;
- parameter-neighborhood stability;
- cost break-even level;
- regime and subperiod concentration; and
- final-test and forward-evidence results as distinct sections.

Undefined or statistically unsupported values fail closed and are never coerced into a favorable score.

## Research promotion policy

A candidate is labelled **research champion eligible** only when all defaults below pass:

- at least 300 independent closed trades;
- positive median net return and net Sharpe across walk-forward folds;
- positive total net return under doubled estimated costs;
- Deflated-Sharpe probability at least 0.99;
- bootstrap probability of positive net edge at least 0.99;
- probability of backtest overfitting at most 0.10;
- parameter-neighborhood stability at least 0.80;
- maximum drawdown no worse than the configured 10% research cap;
- no single fold, month, regime, or asset contributes a majority of total profit;
- positive sealed final-test result after costs;
- all causal, provenance, completeness, and execution audits pass; and
- material conservative-score improvement over the incumbent champion.

These thresholds are minimum defaults, not proof of future profitability. Users may make research thresholds stricter. Weakening them creates a visibly non-promotable exploratory run and cannot change readiness or live permissions.

The champion remains research-only. Shadow, paper, and live readiness continue through the existing broker-safe control plane. At minimum, a new champion starts a new forward cohort and must accumulate the configured number of sessions and trades without learner mutation. Existing hard capital caps, manual short-lived arming, reconciliation, emergency stop, and live lock remain authoritative.

## Confidence and signal behavior

The user-facing confidence value becomes a conservative decision score, not a promise of correctness. It combines calibrated directional probability, expected net edge after costs, ensemble breadth, regime familiarity, uncertainty, data freshness, and execution feasibility.

The engine emits long or short only when:

- expected net edge exceeds costs plus an uncertainty buffer;
- calibrated probability clears the direction-specific threshold;
- minimum independent strategy breadth agrees;
- the current regime is represented in validation evidence;
- market data, spread, borrow, account, and reconciliation state are fresh; and
- all risk controls permit the proposed exposure.

Otherwise it emits abstain with explicit reasons. Position size falls as uncertainty, costs, drawdown, correlation, or unfamiliarity rises. Abstention rate and false-positive cost are first-class quality metrics.

## Persistence and auditability

Schema v5 adds append-only records for:

- deep-research runs and pre-registered protocols;
- dataset manifests and coverage defects;
- trial attempts and semantic candidate identities;
- fold and stress metrics;
- worker lifecycle events and checkpoints;
- champion comparisons and promotion decisions; and
- resource samples and termination reasons.

Natural identities include dataset hash, code hash, protocol hash, search-space hash, candidate hash, candidate version, symbol, feed, interval, run mode, seed, and trial ordinal. Completed evidence is never overwritten. Large curves and checkpoints use content-addressed artifacts referenced from SQLite, with an integrity hash and schema version.

## CLI and engine protocol

The Python CLI adds:

```text
strategy deep-research --symbol SYMBOL --interval INTERVAL --provider PROVIDER
                       --feed FEED --workers N --budget N|unbounded
                       --time-budget SECONDS --resume RUN_ID
```

`--budget unbounded` means continue until the user stops the run; each cycle remains finite and checkpointed. The CLI emits newline-delimited progress events for stage, completed/total trials, generation, champion score, worker count, thermal pause requested by the host, elapsed time, and checkpoint ID. Control uses a run-scoped command file with authenticated ownership and atomic state transitions for pause, resume, and stop.

The packaged engine exposes identical behavior. Snapshot export occurs at safe checkpoints and at termination.

## Native macOS experience

Strategy Lab gains a prominent **Start Deep Research** button. A native configuration sheet selects asset, interval, provider/feed, Balanced/Performance/Custom resources, finite or continuous duration, and available-history coverage. Defaults are safe and require no finance expertise.

While running, the workspace shows:

- stage and elapsed time;
- trials, generations, worker utilization, memory estimate, and thermal state;
- incumbent champion versus leading challenger;
- walk-forward, stress, and final-test gates;
- trial-aware overfitting diagnostics;
- data coverage and unavailable sources;
- pause, resume, and stop controls; and
- a persistent notice that research does not place trades.

Completion shows one of three honest outcomes: **No reliable strategy found**, **Research champion found—forward testing required**, or **Existing champion retained**. Backtest cards label all simulated results and keep hypothetical, paper, and live evidence visually distinct. VoiceOver labels, keyboard operation, reduced-motion behavior, semantic system colors, scalable layouts, and standard macOS controls remain required.

## Failure handling

- Provider failure preserves prior authenticated cache and marks the requested range incomplete.
- One worker crash is retried once; repeat failure becomes a recorded failed trial.
- Database writes are single-writer transactions and resume from the last committed ordinal.
- Hash mismatch, schema mismatch, or corrupt checkpoint prevents resume.
- Thermal critical, memory guard, disk guard, sleep, or user pause stops new work and checkpoints safely.
- User Stop never discards completed trials.
- App or engine termination leaves a resumable run unless artifact integrity failed.
- NaN, infinity, impossible fills, inconsistent timestamps, and zero-cost assumptions fail the affected evaluation closed.

## Security and privacy

Deep Research runs locally. Broker secrets remain in Keychain and are supplied ephemerally only to jobs that require authenticated market data. Secrets are excluded from command lines, snapshots, checkpoints, manifests, logs, crash reports, and trial payloads. Workers receive only normalized research data and typed candidates. Imported files remain local unless the user separately configures a provider upload; this feature adds no upload path.

## Testing and verification

Python tests must cover:

- deterministic results across worker counts and resume boundaries;
- immutable trial counting and semantic deduplication;
- single-writer ordering under out-of-order worker completion;
- worker crash, pause, stop, corrupt checkpoint, and resource guards;
- dataset coverage/provenance and point-in-time joins;
- nested purging, embargo, sealed final-test isolation, and prefix invariance;
- execution costs, partial fills, borrow/funding, adverse ordering, and stress matrices;
- Deflated Sharpe, overfitting probability, bootstrap, multiple-testing, and stability gates;
- champion replacement and failure-to-promote paths;
- inability of Deep Research to submit or arm broker orders; and
- snapshot/CLI/package parity.

Swift tests must cover typed invocations, resource limits, progress decoding, pause/resume/stop, thermal-state presentation, accessibility copy, outcome states, evidence separation, and snapshot compatibility. Full Python and Swift suites, formatting, linting, secret/history scan, packaged-engine build, app build, manifest verification, SBOM generation, and release gates must pass before publication.

## Acceptance criteria

The work is complete when:

1. A user can start, observe, pause, resume, and stop a reproducible Deep Research run from the macOS app.
2. Performance mode exercises all active processors without oversubscription and safely responds to thermal/resource pressure.
3. Every compatible authenticated observation is included or an exact unavailability reason is shown.
4. Search repeatedly produces challengers while protecting all unseen and forward evidence from feedback.
5. Every attempted candidate is counted and robust statistical gates prevent selection by headline backtest profit.
6. Results are deterministic for a fixed dataset, protocol, seed, and worker count-independent schedule.
7. A promoted research champion passes every configured gate; otherwise the app clearly reports that no reliable strategy was found.
8. No Deep Research path can submit a broker order, weaken the live lock, or relabel simulated performance.
9. The implementation and beginner documentation make the limits of hypothetical performance unmistakable.

## Research basis

- Bailey and López de Prado, *The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and Non-Normality*: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551>
- Bailey et al., *The Probability of Backtest Overfitting*: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253>
- Harvey and Liu, *Backtesting*: <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2345489>
- Apple Foundation `ProcessInfo`: <https://developer.apple.com/documentation/foundation/processinfo>
- FINRA Day-Trading Risk Disclosure: <https://www.finra.org/rules-guidance/rulebooks/finra-rules/2270>
- CFTC trading-system warning: <https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/fraudadv_tradingsystem.html>
