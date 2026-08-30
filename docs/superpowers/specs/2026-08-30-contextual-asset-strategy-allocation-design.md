# Contextual Asset Selection and Strategy Allocation Design

**Date:** 2026-08-30  
**Status:** Approved in chat; awaiting written-spec review  
**Scope:** Point-in-time asset eligibility, asset- and regime-conditioned strategy weighting, covariance-aware opportunity selection, governed contextual learning, portfolio validation, and native macOS evidence presentation

## Purpose

Nowcaster will decide not only whether a strategy has credible evidence, but also whether that strategy is appropriate for the exact asset, interval, direction, session, liquidity state, and market regime currently observed. It will prioritize the conservative lower bound of expected net edge after all modeled costs and uncertainty. The system may conclude that no asset or strategy is presently eligible.

The purpose is to reduce avoidable false positives and concentrate research attention on liquid, executable situations where an independently validated specialist ensemble has evidence. This design cannot make profit reliable in the everyday meaning of guaranteed income. It makes asset selection, specialization, uncertainty, costs, portfolio interactions, and failures explicit and testable.

## Approved architectural choice

The implementation will use a **hierarchical specialist ensemble** rather than a separate unconstrained optimizer for every symbol or one opaque global model.

The hierarchy is:

```text
global
  -> asset class
    -> asset profile
      -> symbol + interval + direction
        -> causal market regime
```

Local evidence is partially pooled with its parent. A sparse local context therefore inherits conservative broad evidence rather than overreacting to a few successful trades. A well-supported local context may diverge from its parent, subject to concentration, covariance, turnover, and multiple-testing controls.

## Non-negotiable invariants

1. No future observation, revised value unavailable at the time, or terminal universe membership may influence an earlier eligibility, regime, weight, or portfolio decision.
2. The full selection pipeline is trained again inside every chronological validation fold. A screen chosen with final-test results is invalid.
3. Preconfigured tickers are candidates, not automatically eligible assets.
4. Missing quote, depth, cost, shortability, funding, calendar, or data-quality evidence causes abstention for the affected action.
5. Spot assets without an authenticated short mechanism never generate an executable short alert.
6. Every asset, strategy, parameter, regime, direction, and model variant evaluated consumes the immutable global trial ledger.
7. Local weights shrink toward broader priors as effective sample size falls.
8. Strategy and family caps, a cash/abstention option, and minimum effective breadth prevent forced concentration.
9. Live learning consumes only resolved, causally available outcomes and cannot rewrite prior decisions.
10. A challenger remains shadow or paper evidence until the existing promotion and forward-readiness gates pass.
11. Confirmed drift, stale evidence, unhealthy market data, or failed optimization invalidates the affected context and fails closed.
12. This wave does not submit, replace, or cancel broker orders and does not unlock autonomous live execution.

## Definitions

### Asset profile

An `AssetProfile` describes structural execution and session behavior, not profitability. Initial profiles are:

- `us_liquid_equity`: exchange session, equity product, borrow-dependent shorts;
- `us_broad_etf`: exchange session, ETF-specific session rules, borrow-dependent shorts;
- `crypto_major_spot`: continuous session, spot long or flat only; and
- `crypto_liquid_derivative`: continuous session, derivative shorts permitted only with authenticated funding and venue evidence.

An instrument references exactly one profile. Profiles declare applicable calendars, products, directions, strategy families, session specialists, minimum history, and hard liquidity limits. Performance weights are learned; the profile does not encode a claim that trend or reversal must work.

### Eligibility evidence

An `AssetEligibilityEvidence` record is an immutable point-in-time result for one provider, feed, venue, product, symbol, interval, direction, and decision timestamp. It contains the inputs, hard-gate outcomes, diagnostics, configuration hash, source-event watermark, and final state:

- `eligible`: may enter the strategy ensemble and portfolio candidate set;
- `watch`: structurally supported but insufficiently evidenced or temporarily outside preferred conditions; or
- `blocked`: not executable or unsafe with current evidence.

### Regime posterior

A `RegimePosterior` is a probability vector, not a hindsight label. It contains probabilities for:

- `trend_normal`;
- `trend_elevated_volatility`;
- `range_liquid`; and
- `stressed_or_illiquid`.

Probabilities sum to one. The feature and fit timestamps, training boundary, model version, calibration evidence, and source watermark are persisted. If the posterior is unavailable or too uncertain, the ensemble uses the non-regime parent with an uncertainty penalty; it never fabricates a confident regime.

### Context key

A `StrategyContextKey` contains:

```text
dataset_hash
protocol_hash
provider / feed / venue / product
asset_class / asset_profile / symbol
interval / direction
regime or parent-level marker
mode
```

Context keys are immutable, canonical, and hashable. Evidence from different feeds, products, intervals, directions, data snapshots, or protocol versions never pools silently.

## 1. Point-in-time trading universe

### Candidate universe

The system evaluates only explicitly configured instruments, an explicit user watchlist, or instruments returned by a configured provider route. Provider-wide discovery is bounded by a configurable maximum and never silently adds assets to an active monitor.

The bundled candidate universe remains intentionally small. Existing BTCUSDT and ETHUSDT instruments become `crypto_major_spot` candidates. Equity and ETF candidates use Alpaca-compatible metadata only when that provider is configured. No static list is described as the historically best or most profitable list.

### Hard eligibility gates

Each decision checks, using observations available by that decision time:

- instrument metadata and exact product identity;
- listing and delisting interval;
- trading status, halt state, and session state;
- required finalized-history length and contiguous warm-up coverage;
- current feed freshness, sequence continuity, and correction state;
- minimum rolling median notional volume;
- maximum quoted relative spread;
- minimum displayed depth near the midpoint when depth is required;
- maximum estimated participation and price impact for the configured research size;
- minimum price relative to tick size and valid lot size;
- realized-volatility lower and upper bounds;
- shortability, easy-to-borrow, or derivative mechanism for a short context;
- authenticated borrow fee or funding applicability where relevant; and
- provider/feed parity between research evidence and the live decision.

Thresholds live in a typed `AssetSelectionConfig` and may be stricter per profile. They are versioned and included in every evidence hash. Weakening a promotion-grade limit makes the resulting run exploratory rather than silently changing the production policy.

### Diagnostic score

After hard gates pass, a bounded diagnostic quality score summarizes coverage, liquidity, spread, depth, impact, capacity, and freshness. The score sorts eligible research candidates but cannot override a failed hard gate. It is not a probability of profit.

## 2. Causal market-regime model

### Inputs

Regime features use only finalized, lagged data and include:

- multi-horizon normalized trend slope;
- directional consistency and ADX-like trend strength;
- short- and medium-window realized volatility percentile;
- volatility-of-volatility;
- relative notional volume and volume concentration;
- relative spread and depth imbalance when authenticated;
- market or cross-sectional breadth when a causal universe is available;
- session phase; and
- gap, halt, and continuity indicators.

Every rolling transform uses past-only windows and is prefix invariant. Cross-sectional inputs require point-in-time membership and contemporaneous availability for every included member.

### Model

The first implementation uses a small regularized multinomial model with expanding chronological fits and out-of-fold probability calibration. The broad four-regime taxonomy is fixed by policy; an optimizer cannot create dozens of tiny regimes. Regime probabilities are blended into strategy estimates rather than used as a hard switch:

```text
context estimate = sum(regime_probability[r] * estimate[r])
```

`stressed_or_illiquid` increases uncertainty and normally routes mass toward abstention. It cannot increase exposure merely because a small historical crisis sample produced a high return.

## 3. Hierarchical evidence estimates

### Local net-outcome observations

Every strategy contributes synchronized out-of-fold net outcomes with:

- decision and outcome-availability timestamps;
- signal and direction;
- realized return;
- fee, spread, slippage, impact, borrow, and funding cost;
- regime posterior known at the decision;
- asset-eligibility identity; and
- provenance linking the exact strategy evaluation and trial ledger.

Only promotion-grade, causally audited component evaluations enter promotable contextual weights. Exploratory evidence may be displayed but has zero production influence.

### Partial pooling

For strategy `s` in context `c`, a local estimate shrinks toward its parent:

```text
alpha_c = n_effective_c / (n_effective_c + prior_strength_c)
mu_c = alpha_c * mu_local_c + (1 - alpha_c) * mu_parent_c
```

`n_effective` discounts serial correlation and duplicated exposure. `mu_local` and `mu_parent` are conservative net-edge estimates. The estimator also carries uncertainty, lower confidence bound, calibration error, coverage, cost error, and drawdown contribution. If no valid parent exists, the prior is cash with zero expected return rather than an optimistic constant.

Parent strength is preconfigured by hierarchy level and may be tuned only inside development folds. The chosen value and all alternatives count in the global trial ledger.

### Direction asymmetry

Long and short evidence is estimated separately. A strategy profitable long on one asset cannot grant weight to its short form. Short-context estimates include availability, borrow, squeeze, funding, and asymmetric tail-risk evidence.

## 4. Covariance-aware strategy allocation

### Inputs

For every eligible context the allocator receives:

- lower-confidence-bound net edge `mu_lcb` per strategy;
- synchronized out-of-fold strategy net-return matrix;
- hierarchical prior weights;
- previous causally effective weights;
- strategy family and applicability;
- uncertainty and cost-model error; and
- strategy, family, and effective-breadth constraints.

### Covariance

The strategy covariance matrix is estimated from synchronized out-of-fold net returns using Ledoit-Wolf shrinkage. If overlap is insufficient, the allocator shrinks toward a diagonal parent estimate. Non-finite, asymmetric, or non-positive-semidefinite results fail closed. Covariance provenance records the aligned timestamps and estimator parameters.

### Optimization

The deterministic convex objective is:

```text
minimize_w
    - mu_lcb' w
    + risk_penalty * w' covariance w
    + turnover_penalty * ||w - previous_w||^2
    + prior_penalty * ||w - hierarchical_prior||^2
```

Subject to:

- `w >= 0`;
- strategy-specific maximum weights;
- family-specific maximum weights;
- inapplicable or ineligible strategies have weight zero;
- active risk weights plus an explicit cash/abstention weight sum to one;
- a configured minimum effective number of non-cash strategies; and
- only strategies with positive conservative net edge may receive risk weight.

The effective number is `1 / sum(w_i^2)` over non-cash strategies. If constraints are infeasible, covariance evidence is insufficient, optimization fails, or validation of the result fails, all risk mass moves to cash. There is no fallback that ignores costs or caps.

The implementation uses SciPy's deterministic constrained optimizer with fixed tolerances and sorted inputs. The result is independently validated for feasibility and canonicalized before persistence. Repeated runs over identical evidence must be bitwise stable after canonical numeric rounding.

### Decision combination

The current ensemble's calibrated-probability, breadth, vote-margin, cost, and uncertainty gates remain. Contextual weights add these gates:

- eligible asset and direction;
- supported profile and strategy applicability;
- adequate local-or-parent evidence;
- regime posterior available or conservative parent fallback;
- positive lower net-edge bound;
- adequate effective strategy count;
- covariance and optimization evidence authenticated; and
- no contextual drift quarantine.

## 5. Portfolio opportunity selector

An asset-level decision does not automatically become a surfaced opportunity. A `PortfolioResearchSelector` receives all contemporaneous eligible decisions and solves a second conservative allocation problem.

### Candidate score

The primary ranking input is the lower confidence bound of expected net edge after costs and uncertainty. Probability, vote margin, and diagnostic liquidity quality break ties but never compensate for non-positive lower net edge.

### Portfolio constraints

- maximum gross and net research exposure;
- maximum asset, strategy-family, asset-class, and sector exposure;
- maximum number of concurrent opportunities;
- correlation-cluster cap using shrinkage covariance;
- no duplicate symbol/direction/horizon risk windows;
- no simultaneous conflicting directions for one product;
- volume-participation and estimated-impact capacity;
- drawdown and daily-loss budget inherited from the risk policy; and
- explicit cash allocation.

The selector may choose zero opportunities. An accepted candidate receives a research-size ceiling equal to the smallest of:

- volatility-target size;
- liquidity-capacity size;
- portfolio-risk remaining size;
- a strongly capped fractional-Kelly estimate calculated from conservative probability and payoff bounds; and
- the existing hard asset and gross-exposure limits.

The size is explanatory research output. It does not place an order and must be shown as a ceiling, not a recommendation.

## 6. Nested validation and backtesting

### Full-pipeline nesting

Each outer fold must reproduce the complete historical decision process:

1. Resolve the point-in-time candidate universe.
2. Fit eligibility normalizers on the training block.
3. Fit and calibrate the regime model on training evidence.
4. Generate or tune strategies using inner folds only.
5. Produce synchronized outer-fold component predictions.
6. Build hierarchical estimates using evidence available at the fold boundary.
7. Estimate shrinkage covariance and contextual weights.
8. Calibrate the asset-level ensemble.
9. Run the portfolio opportunity selector.
10. Simulate next-actionable-bar execution with contemporaneous costs and capacity.

The sealed final test is consumed once by a frozen protocol and candidate cohort. A new asset screen, regime taxonomy, strategy candidate, weight penalty, or portfolio constraint creates a new trial identity.

### Historical-universe integrity

Equity validation requires point-in-time listing, delisting, split, dividend/adjustment, symbol-change, session, and membership evidence appropriate to the dataset. Crypto validation requires point-in-time listing status, venue/product continuity, quote-asset identity, fee/funding applicability, and known data gaps. When this evidence is unavailable, the affected screen receives a lower evidence grade and cannot claim complete-universe promotion.

### Required metrics and slices

Metrics are reported at strategy, context, asset, and portfolio levels:

- net return, Sharpe, Sortino, expected shortfall, and maximum drawdown;
- lower net-edge confidence bound and break-even costs;
- nominal and effective observations and trades;
- turnover, participation, impact, capacity, and missed fills;
- Brier score, calibration error, log loss, precision, coverage, and risk-coverage curve;
- Deflated Sharpe probability, global trial count, bootstrap probability, and PBO;
- effective strategy count and covariance concentration;
- contribution by asset, strategy, direction, regime, year, month, and session; and
- comparison with cash, applicable passive baselines, equal-weight specialists, and the frozen incumbent.

Required stress cases include existing cost/liquidity/fill/parameter/bootstrap scenarios plus:

- universe entry and exit perturbation;
- regime-classification uncertainty;
- correlation spike;
- borrow or funding shock;
- spread and impact forecast error;
- top asset and top strategy removed;
- worst asset/regime block repeated; and
- portfolio capacity reduced by half.

Promotion fails if profit is dominated by one asset, one context, one short interval, or a small number of best trades beyond configured limits.

## 7. Governed contextual learning

Learning mode extends its closed grammar to search:

- asset-profile applicability filters;
- direction-specific strategy parameters;
- causal regime interactions;
- entry confirmation and cooldown rules;
- holding horizons and time stops;
- empirically supported stop, target, and trailing policies;
- hierarchical prior strengths;
- covariance risk, turnover, and prior penalties; and
- conservative portfolio thresholds.

The learner may use deterministic successive halving and bounded evolutionary mutation. It cannot create source code, unrestricted expressions, new regimes, broker actions, or arbitrary assets. Complexity, turnover, low coverage, context fragmentation, and portfolio concentration are penalized.

Self-improvement follows:

```text
candidate -> nested development -> sealed test -> shadow -> paper -> forward-qualified
```

No stage can be skipped. Continuous learning waits for newly resolved outcomes and creates a new challenger version; it does not repeatedly mine the same sealed segment.

## 8. Live contextual adaptation

The current authenticated specialist fixed-share update remains the online adaptation mechanism. It is extended to contextual cells and then shrunk toward the frozen hierarchical prior.

- Outcomes update only the exact provider/feed/product/symbol/interval/direction context that produced them.
- Soft regime credit is divided according to the posterior stored with the original decision.
- An outcome is processed only after `outcome_available_at` and only once.
- Adaptive learning rates remain bounded by policy.
- Sparse cells retain parent influence.
- Warning drift reduces local influence and widens uncertainty.
- Confirmed drift quarantines the cell, invalidates readiness, and routes weight to the parent or cash.
- Recovery requires fresh shadow or paper evidence under a new immutable cohort.

Replay from the persisted outcome ledger must reproduce identical weights, watermarks, and hashes after restart.

## 9. Configuration and storage

### Configuration

Add `config/asset_selection.yaml` with:

- profile definitions and instrument-to-profile bindings;
- candidate-universe limits;
- profile-specific history, liquidity, spread, depth, impact, volatility, and capacity gates;
- regime feature windows and regularization policy;
- hierarchy prior strengths;
- covariance and optimizer penalties;
- portfolio concentration limits; and
- evidence-grade and promotion restrictions.

Pydantic models are frozen, reject extra fields, validate cross-field consistency, and serialize canonically. Existing instrument declarations remain backward compatible but promotable contextual runs require an explicit profile.

### Additive persistence

Add append-only tables for:

- asset eligibility evidence;
- regime posteriors;
- hierarchical context estimates;
- contextual covariance snapshots;
- contextual ensemble weights;
- portfolio research decisions and exclusions; and
- contextual drift and outcome attribution.

Natural identities include context hash, evidence timestamp, dataset/protocol/code/config hashes, source watermark, and cohort identity. Existing `ensemble_weights` rows remain readable. New consumers prefer contextual evidence when a complete authenticated cohort exists; partial contextual records never override a valid legacy cohort.

## 10. Engine, CLI, snapshot, and macOS app

### Engine and CLI

Add commands that support:

```text
strategy screen-universe --as-of ...
strategy evaluate-contexts --symbols ... --interval ... --mode ...
strategy backtest-portfolio --universe ... --interval ...
strategy learn-contextual --universe ... --interval ... --budget ...
```

Commands emit deterministic progress events for screening, regime fitting, strategy evaluation, hierarchical estimation, covariance, portfolio simulation, stress testing, and publication. The packaged engine exposes the same protocol.

### Snapshot contract

Snapshots add optional backward-compatible fields for:

- asset eligibility state and reasons;
- asset profile and selection quality;
- spread, depth, impact, capacity, and data coverage;
- regime probabilities and uncertainty;
- local, parent, and final strategy weights;
- effective sample and effective strategy counts;
- covariance/concentration status;
- portfolio rank, research-size ceiling, correlation conflicts, and exclusion reasons; and
- contextual drift state.

Python and Swift fixtures must remain schema-parity tested. Unknown enum cases remain non-crashing and visible.

### Native experience

Markets gains eligibility, profile, regime, and liquidity evidence. Signal detail gains **Why this asset now**, local-versus-parent strategy influence, conservative net-edge decomposition, and portfolio conflicts. Strategy Lab gains a universe-wide contextual research action and shows progress without blocking live monitoring.

Beginner-facing copy distinguishes:

- model confidence from calibrated probability;
- probability of the defined outcome from probability of profit;
- backtest evidence from forward evidence;
- an eligible research setup from an order; and
- a research-size ceiling from investment advice.

The interface remains native SwiftUI and follows the existing accessibility, keyboard, reduced-motion, semantic-color, and responsive-layout contracts.

## Failure behavior

- No eligible assets: publish an empty opportunity set with per-asset reasons.
- Insufficient local evidence: use the authenticated parent with an uncertainty penalty or cash.
- Regime unavailable: use the non-regime parent or abstain; never infer from future data.
- Covariance unavailable or optimizer failure: allocate all risk mass to cash.
- Portfolio constraints infeasible: surface no opportunity.
- Missing borrow/funding evidence: block the short context.
- Missing historical membership or listing evidence: lower evidence grade and block complete-universe promotion.
- Feed or sequence failure: freeze the affected scope without affecting unrelated scopes.
- Contextual drift: quarantine only the affected cell, invalidate its readiness, and preserve the audit trail.
- No challenger passes: retain the incumbent and report that no reliable improvement was found.

## Testing strategy

### Unit and property tests

- Future-tail mutation cannot change earlier screens, regimes, weights, selections, orders, or fills.
- Point-in-time membership, listing, halt, and shortability gates reject invalid contexts.
- Strategy applicability is deterministic per profile, direction, product, and session.
- Regime probabilities are finite, normalized, calibrated out of fold, and prefix invariant.
- Partial pooling approaches the parent at zero local evidence and the local estimate as effective evidence grows.
- Long evidence cannot leak into short weights.
- Shrinkage covariance is symmetric, finite, positive semidefinite, and deterministic.
- Optimized weights obey all caps, sum with cash to one, and fail to cash when infeasible.
- Duplicate strategies and highly correlated components cannot create false breadth.
- Portfolio selection respects correlation, concentration, capacity, and conflicting-direction constraints.
- Trial identities change for every searched degree of freedom.
- Contextual online replay is idempotent and restart deterministic.
- All new provenance and cohort hashes reject tampering.

### Integration tests

- Full nested walk-forward screen-to-portfolio simulation on deterministic fixtures.
- Historical-universe entry, exit, delisting, and missing-membership scenarios.
- Equity and crypto profile paths, including impossible spot shorts.
- Live quote/depth eligibility through notification planning and lifecycle close.
- Drift quarantine and parent/cash fallback.
- Database migration and legacy-read compatibility.
- Snapshot export and Swift decoding parity.
- Strategy Lab contextual run progress, cancellation, export, and reload.

### Required completion verification

- Full Python test suite and coverage-sensitive targeted suites.
- Ruff format and lint.
- Swift package tests and release build.
- Deterministic replay and prefix-invariance audits.
- Full contextual backtest fixture with trial-accounting audit.
- Database migration/reopen checks.
- Snapshot schema parity and accessibility tests.
- Engine bundle, release verification, and secret scan.

## Acceptance criteria

1. Every candidate asset receives point-in-time eligible, watch, or blocked evidence with explicit reasons.
2. Preconfigured membership alone cannot make an asset eligible.
3. Strategies are filtered by structural applicability and weighted by causal asset-, direction-, interval-, and regime-specific evidence.
4. Sparse contexts demonstrably shrink toward broader priors.
5. Strategy-return covariance affects weights and prevents correlated indicators from masquerading as diversification.
6. Cash/abstention can receive 100% weight whenever conservative edge is absent or constraints fail.
7. The entire screen and allocation path is nested inside chronological validation and passes no-repaint tests.
8. Every searched asset, rule, parameter, regime interaction, and allocation policy is globally trial-accounted.
9. Portfolio backtests include costs, capacity, overlap, correlation, exposure, and concentration.
10. Live adaptation is context-authenticated, outcome-delayed, shrinkage-controlled, drift-aware, and replayable.
11. The macOS app explains why an asset and strategy were selected or rejected without presenting certainty.
12. Existing live safety, manual control, and autonomous-order lock remain intact.

## Research basis

- Moreira and Muir, *Volatility Managed Portfolios* — volatility-conditioned risk can improve risk-adjusted outcomes, but must be validated per context.
- Dai, Medhat, Novy-Marx, and Rizova, *Reversals and the Returns to Liquidity Provision* — short-run reversal behavior varies with volatility, turnover, and liquidity.
- Gu, Kelly, and Xiu, *Empirical Asset Pricing via Machine Learning* — nonlinear momentum, liquidity, and volatility interactions are useful candidate features.
- Ledoit and Wolf, *Honey, I Shrunk the Sample Covariance Matrix* — covariance shrinkage reduces optimizer sensitivity to estimation error.
- Blanc and Setzer, *Bias-Variance Trade-Off and Shrinkage of Weights in Forecast Combination* — forecast weights benefit from shrinkage rather than unconstrained historical optimization.
- Herbster and Warmuth, *Tracking the Best Expert* — fixed-share online learning supports changing expert performance without permanently eliminating specialists.
- Harvey, Liu, and Zhu, *... and the Cross-Section of Expected Returns* — large strategy searches require explicit multiple-testing controls.
- Niculescu-Mizil and Caruana, *Predicting Good Probabilities with Supervised Learning* — ranking performance is not enough; action probabilities require independent calibration.
- Gangrade, Kag, and Saligrama, *Selective Classification via One-Sided Prediction* — abstention is a principled way to trade coverage for a lower accepted-error rate.

## Explicit non-goals

- Guaranteeing profit, a minimum win rate, or a fixed income.
- Selecting assets with hindsight or optimizing against the final test.
- Adding dozens of indicators without independent economic and statistical evidence.
- Unbounded reinforcement learning, unrestricted generated code, or self-deployment.
- Treating synthetic simulations as historical or forward proof.
- Increasing signal frequency at the expense of expected net value.
- Automatically placing trades or weakening the existing live lock.
