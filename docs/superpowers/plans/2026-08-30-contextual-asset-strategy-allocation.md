# Contextual Asset Selection and Strategy Allocation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a causal, point-in-time system that selects executable assets, learns strategy influence by asset/direction/regime, penalizes correlated evidence, and surfaces only portfolio-compatible research opportunities.

**Architecture:** Add a focused `src/contextual` package beside the existing strategy engine. Pure modules own eligibility, regime probabilities, hierarchical estimates, constrained allocation, portfolio selection, online replay, and persistence; `StrategyPipeline` supplies authenticated outcomes, while a contextual service orchestrates database-backed runs. Existing live-monitor, snapshot, and SwiftUI contracts consume optional authenticated contextual evidence and fail closed when it is absent.

**Tech Stack:** Python 3.11+, dataclasses, Pydantic 2, pandas, NumPy, scikit-learn `LogisticRegression`/`LedoitWolf`, SciPy SLSQP, SQLAlchemy/DuckDB, Typer, pytest, Swift 6, SwiftUI, Swift Testing.

**Spec:** `docs/superpowers/specs/2026-08-30-contextual-asset-strategy-allocation-design.md`

**Completion:** All 14 implementation tasks are complete, locally verified and published to `main`. See [release verification](../../contextual-release-verification.md) for the evidence and the outstanding external visual-inspection, distribution-signing and market-validation limitations.

**Release audit clarification (30–31 August 2026):** Tasks 1–13 are implemented. Task 14 adds exact cost/direction accounting, publication-time and mirror authentication, a non-publishing historical replay, shared-capital learning, fresh verified depth, and persistent drift/expiry checks. Real packaged-market verification additionally required current Binance permission handling, verified bundled TLS roots, a preserving schema-14 migration for exchange sequence IDs, cancellable private input and supervised shutdown. The contextual portfolio replay is explicitly retrospective fixed-policy walk-forward research, not an independent sealed/nested-optimization result. Search considers only holding horizons backed by actual execution outcomes. No evidence threshold was lowered to make fixtures pass.

## Global Constraints

- Only finalized observations available by the decision timestamp may affect eligibility, regimes, weights, selection, or live alerts.
- Context identity includes dataset/protocol/provider/feed/venue/product/profile/symbol/interval/direction/regime/mode; incompatible evidence never pools silently.
- The complete screen, regime, strategy, weighting, and portfolio path must be fitted again inside chronological validation folds.
- Missing quote, depth, cost, shortability, funding, calendar, optimization, or drift evidence fails closed for the affected action.
- Every searched asset, strategy, parameter, regime interaction, and allocation policy consumes the global trial ledger.
- Existing strategy/family caps, promotion gates, manual controls, notification-only monitor, and autonomous-order lock remain authoritative.
- New snapshot fields remain optional so existing schema-v5 files decode.
- All implementation changes use TDD: observe the focused test fail before writing production code.
- Reuse the existing NumPy, pandas, scikit-learn and SciPy floors. Final transport verification makes the already-installed `certifi` certificate bundle an explicit runtime dependency; TLS verification must not be disabled to accommodate missing system roots.

---

## File map

### New Python package

- `src/contextual/__init__.py`: public contextual API only.
- `src/contextual/types.py`: enums and immutable cross-module evidence identities.
- `src/contextual/eligibility.py`: point-in-time asset and direction gates.
- `src/contextual/regimes.py`: causal regime features, chronological fit, posterior inference.
- `src/contextual/hierarchy.py`: effective-sample partial pooling and soft-regime blending.
- `src/contextual/allocation.py`: Ledoit-Wolf covariance and constrained strategy weights.
- `src/contextual/portfolio.py`: opportunity ranking, portfolio constraints, size ceilings.
- `src/contextual/online.py`: authenticated contextual outcome attribution and replay.
- `src/contextual/repository.py`: canonical append-only database rows.
- `src/contextual/service.py`: screen/evaluate/backtest/learn orchestration.

### Existing Python files

- `src/config/settings.py`, `config/asset_selection.yaml`, `config/instruments.yaml`: typed policy and profile bindings.
- `src/database/schema.py`, `src/database/engine.py`: schema-v13 additive tables.
- `src/strategies/pipeline.py`: publish resolved component outcomes to contextual storage.
- `src/live_monitor/engine.py`, `src/live_monitor/evidence.py`: live contextual eligibility and drift gates.
- `src/learning/search.py`, `src/deep_research/contracts.py`: globally identified contextual trials.
- `src/cli.py`: four contextual strategy commands.
- `src/app_snapshot/models.py`, `src/app_snapshot/builder.py`: optional contextual projections.
- `README.md`, `docs/strategy-methodology.md`, `docs/live-monitor.md`: beginner-readable behavior and limitations.

### Existing Swift files

- `macos/Nowcaster/Sources/NowcasterApp/Models/Snapshot.swift`: optional contextual fields.
- `macos/Nowcaster/Sources/NowcasterApp/Models/EngineJob.swift`: contextual research invocation.
- `macos/Nowcaster/Sources/NowcasterApp/Features/Markets/MarketsView.swift`: eligibility/regime columns.
- `macos/Nowcaster/Sources/NowcasterApp/Features/Markets/InstrumentDetailView.swift`: asset-selection evidence.
- `macos/Nowcaster/Sources/NowcasterApp/Features/Signals/SignalDetailView.swift`: “Why this asset now” and portfolio evidence.
- `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/StrategyLabView.swift`: contextual research action and status.

---

### Task 1: Immutable contextual configuration and identities

**Files:**
- Create: `src/contextual/__init__.py`
- Create: `src/contextual/types.py`
- Create: `config/asset_selection.yaml`
- Modify: `src/config/settings.py:80-190,248-305`
- Modify: `config/instruments.yaml`
- Test: `tests/unit/test_contextual_config.py`
- Test: `tests/unit/test_config.py`

**Interfaces:**
- Produces: `AssetProfileName`, `EligibilityState`, `MarketRegime`, `StrategyDirection`, `StrategyContextKey`, `ProfilePolicy`, `AssetSelectionConfig`.
- Consumers: every subsequent contextual task and `Settings.asset_selection`.

- [x] **Step 1: Write failing configuration and identity tests**

```python
def test_checked_in_assets_have_explicit_profiles() -> None:
    settings = Settings.load(PROJECT_ROOT, mode="test")
    assert {item.symbol: item.profile.value for item in settings.instruments.instruments} == {
        "BTCUSDT": "crypto_major_spot",
        "ETHUSDT": "crypto_major_spot",
    }
    assert settings.asset_selection.profiles[AssetProfileName.CRYPTO_MAJOR_SPOT].allowed_directions == (
        StrategyDirection.LONG,
    )


def test_context_hash_changes_when_direction_or_product_changes() -> None:
    base = context_key(direction=StrategyDirection.LONG, product="spot")
    assert base.context_hash != replace(base, direction=StrategyDirection.SHORT).context_hash
    assert base.context_hash != replace(base, product="perpetual").context_hash
```

- [x] **Step 2: Run tests and verify missing types/config fail**

Run: `pytest tests/unit/test_contextual_config.py tests/unit/test_config.py -q`

Expected: FAIL because `src.contextual.types`, `InstrumentConfig.profile`, and `Settings.asset_selection` do not exist.

- [x] **Step 3: Add immutable types and canonical context hashing**

```python
class AssetProfileName(StrEnum):
    US_LIQUID_EQUITY = "us_liquid_equity"
    US_BROAD_ETF = "us_broad_etf"
    CRYPTO_MAJOR_SPOT = "crypto_major_spot"
    CRYPTO_LIQUID_DERIVATIVE = "crypto_liquid_derivative"


@dataclass(frozen=True, slots=True)
class StrategyContextKey:
    dataset_hash: str
    protocol_hash: str
    provider: str
    feed: str
    venue: str
    product: str
    asset_class: str
    profile: AssetProfileName
    symbol: str
    interval: BarInterval
    direction: StrategyDirection
    regime: MarketRegime | None
    mode: StrategyMode

    @property
    def context_hash(self) -> str:
        return canonical_hash(asdict(self))
```

- [x] **Step 4: Add strict Pydantic policy models and YAML loading**

`ProfilePolicy` must validate finite thresholds, `minimum_realized_volatility < maximum_realized_volatility`, unique allowed directions/families, and positive history/volume/depth. `AssetSelectionConfig` must reject missing profile policies, invalid instrument bindings, `minimum_effective_strategies < 2`, or a maximum strategy weight above the reciprocal breadth requirement. Load `asset_selection.yaml` in `Settings.load()` and include it in `config_hash_payload()` automatically.

- [x] **Step 5: Bind bundled spot instruments to `crypto_major_spot`**

Add `profile: crypto_major_spot` to BTCUSDT and ETHUSDT. The profile allows long only, requires continuous-session identity, and structurally disallows borrow/funding.

- [x] **Step 6: Run focused tests**

Run: `pytest tests/unit/test_contextual_config.py tests/unit/test_config.py tests/unit/test_strategy_registry.py -q`

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add src/contextual config/asset_selection.yaml config/instruments.yaml src/config/settings.py tests/unit/test_contextual_config.py tests/unit/test_config.py
git commit -m "feat: define contextual asset policies"
```

---

### Task 2: Point-in-time asset and direction eligibility

**Files:**
- Create: `src/contextual/eligibility.py`
- Test: `tests/unit/test_contextual_eligibility.py`
- Test: `tests/unit/test_contextual_no_repaint.py`

**Interfaces:**
- Consumes: `ProfilePolicy`, `AssetProfileName`, `EligibilityState`, `StrategyDirection`.
- Produces: `EligibilityInputs`, `AssetEligibilityEvidence`, `evaluate_asset_eligibility(inputs, policy, policy_hash)`, `strategy_is_applicable(spec, instrument, profile, direction, session_phase)`.

- [x] **Step 1: Write failing hard-gate and prefix-invariance tests**

```python
def test_spot_short_and_wide_spread_fail_closed() -> None:
    short = evaluate_asset_eligibility(eligible_inputs(direction="short"), SPOT_POLICY, HASH)
    wide = evaluate_asset_eligibility(eligible_inputs(spread_bps=11), SPOT_POLICY, HASH)
    assert short.state is EligibilityState.BLOCKED
    assert "direction_not_supported" in short.reasons
    assert wide.state is EligibilityState.BLOCKED
    assert "spread_limit" in wide.reasons


def test_session_specialist_is_inapplicable_to_continuous_crypto() -> None:
    assert strategy_is_applicable(OPENING_RANGE, BTC, SPOT_POLICY, StrategyDirection.LONG, "continuous") is False


def test_future_market_rows_cannot_change_prior_eligibility() -> None:
    prefix = eligibility_inputs_from_bars(bars.iloc[:100], as_of=bars.iloc[99].available_at)
    changed = bars.copy()
    changed.loc[100:, "volume"] *= 1_000
    assert eligibility_inputs_from_bars(changed, as_of=bars.iloc[99].available_at) == prefix
```

- [x] **Step 2: Run tests and verify failure**

Run: `pytest tests/unit/test_contextual_eligibility.py tests/unit/test_contextual_no_repaint.py -q`

Expected: FAIL because eligibility interfaces are absent.

- [x] **Step 3: Implement finite, UTC, and chronology validation**

`EligibilityInputs` must require explicit UTC `as_of`, `data_through <= as_of`, finite nonnegative cost/liquidity fields, coverage in `[0,1]`, valid listing/delisting chronology, and a direction supported by the exact instrument product.

- [x] **Step 4: Implement ordered hard gates and non-overriding diagnostic score**

```python
reasons = tuple(dict.fromkeys((*structural_reasons, *data_reasons, *liquidity_reasons)))
state = EligibilityState.BLOCKED if structural_reasons or data_reasons else (
    EligibilityState.WATCH if liquidity_reasons or inputs.liquidity_grade != "observed" else EligibilityState.ELIGIBLE
)
quality = geometric_mean((coverage_score, freshness_score, spread_score, depth_score, impact_score))
```

The score must remain zero for blocked evidence and cannot erase any reason.

`strategy_is_applicable()` must enforce profile families, exact interval support, product direction, session-only strategy IDs, cross-sectional peer requirements, and short mechanism. Applicability is structural; it never looks at returns.

- [x] **Step 5: Implement causal bar-derived inputs**

`eligibility_inputs_from_bars()` must slice `available_at <= as_of`, require finalized bars, compute rolling median notional volume and realized volatility from that slice only, and label bar-derived liquidity `bar_proxy`. It must never synthesize spread or depth as observed evidence.

- [x] **Step 6: Run focused tests**

Run: `pytest tests/unit/test_contextual_eligibility.py tests/unit/test_contextual_no_repaint.py -q`

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add src/contextual/eligibility.py tests/unit/test_contextual_eligibility.py tests/unit/test_contextual_no_repaint.py
git commit -m "feat: gate assets with causal liquidity evidence"
```

---

### Task 3: Soft causal regime probabilities

**Files:**
- Create: `src/contextual/regimes.py`
- Test: `tests/unit/test_contextual_regimes.py`
- Extend: `tests/unit/test_contextual_no_repaint.py`

**Interfaces:**
- Consumes: finalized OHLCV bars and optional contemporaneous spread/depth observations.
- Produces: `REGIME_FEATURE_COLUMNS`, `RegimeFit`, `RegimePosteriorFrame`, `causal_regime_features()`, `fit_regime_model()`, `predict_regime_posteriors()`.

- [x] **Step 1: Write failing causal-feature and posterior tests**

```python
def test_regime_posteriors_are_normalized_and_future_invariant() -> None:
    features = causal_regime_features(bars.iloc[:300])
    fit = fit_regime_model(features.iloc[:220], minimum_train=80)
    before = predict_regime_posteriors(fit, features.iloc[220:250])
    mutated = bars.copy()
    mutated.loc[250:, ["close", "volume"]] *= 100
    after = predict_regime_posteriors(fit, causal_regime_features(mutated).iloc[220:250])
    np.testing.assert_allclose(before.probabilities, after.probabilities)
    np.testing.assert_allclose(before.probabilities.sum(axis=1), 1.0)
```

- [x] **Step 2: Run test and verify missing module failure**

Run: `pytest tests/unit/test_contextual_regimes.py tests/unit/test_contextual_no_repaint.py -q`

- [x] **Step 3: Implement past-only regime features**

Use returns shifted one bar, exponentially weighted trend slope, directional consistency, rolling realized-volatility percentile, volatility-of-volatility, lagged relative volume, and authenticated lagged spread/depth. Every rolling value must exclude the current unfinished interval and preserve input index/timestamps.

- [x] **Step 4: Implement chronological labels and regularized fit**

Training-only quantiles define the broad taxonomy: stressed takes precedence; otherwise trend strength separates trend/range and volatility percentile separates normal/elevated. Fit `StandardScaler` plus `LogisticRegression(C=0.25, class_weight="balanced", random_state=0, max_iter=2_000)` on chronological training rows. Persist class order, feature names, training boundary, scaler coefficients, model coefficients, and a canonical model hash.

- [x] **Step 5: Add conservative fallback**

If fewer than three classes or insufficient observations exist, return an authenticated prior posterior with high `stressed_or_illiquid` mass and `status="parent_fallback"`; never return a one-hot confident regime.

- [x] **Step 6: Run focused tests**

Run: `pytest tests/unit/test_contextual_regimes.py tests/unit/test_contextual_no_repaint.py -q`

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add src/contextual/regimes.py tests/unit/test_contextual_regimes.py tests/unit/test_contextual_no_repaint.py
git commit -m "feat: estimate causal soft market regimes"
```

---

### Task 4: Hierarchical partial pooling and direction isolation

**Files:**
- Create: `src/contextual/hierarchy.py`
- Test: `tests/unit/test_contextual_hierarchy.py`

**Interfaces:**
- Consumes: a validated outcome frame with strategy/context columns, `net_return`, `outcome_available_at`, and four regime-probability columns.
- Produces: `HierarchicalEstimate`, `HierarchyResult`, `build_hierarchical_estimates(outcomes, as_of, prior_strengths)`, `blend_current_regime(estimates, posterior)`.

- [x] **Step 1: Write failing shrinkage, chronology, and direction tests**

```python
def test_sparse_context_shrinks_to_parent_and_dense_context_moves_local() -> None:
    sparse = build_hierarchical_estimates(outcomes(2, local_mean=0.02), AS_OF, STRENGTHS)
    dense = build_hierarchical_estimates(outcomes(500, local_mean=0.02), AS_OF, STRENGTHS)
    assert abs(sparse.leaf("alpha", "AAPL").mean_net_edge - sparse.parent("alpha").mean_net_edge) < 0.003
    assert dense.leaf("alpha", "AAPL").mean_net_edge > sparse.leaf("alpha", "AAPL").mean_net_edge


def test_long_outcomes_never_change_short_estimate() -> None:
    original = build_hierarchical_estimates(frame, AS_OF, STRENGTHS)
    changed = pd.concat([frame, profitable_long_rows(1_000)])
    assert original.leaf("alpha", "AAPL", "short") == build_hierarchical_estimates(
        changed, AS_OF, STRENGTHS
    ).leaf("alpha", "AAPL", "short")
```

- [x] **Step 2: Run tests and verify failure**

Run: `pytest tests/unit/test_contextual_hierarchy.py -q`

- [x] **Step 3: Validate canonical outcome schema and availability**

Reject missing context columns, duplicate outcome identities, non-UTC timestamps, non-finite returns/probabilities, regime probabilities not summing to one, or any `outcome_available_at > as_of` included in fitting.

- [x] **Step 4: Implement weighted effective sample and lower confidence bounds**

Use the existing serial-correlation-aware `effective_sample_size()` on time-ordered net outcomes. For soft regimes, compute weighted means and Kish effective sample size. Use the existing stationary-bootstrap-compatible lower-mean helper where observations permit; otherwise set the local lower bound to the parent or zero cash prior.

- [x] **Step 5: Build levels recursively**

```python
alpha = effective_observations / (effective_observations + prior_strength)
mean = alpha * local_mean + (1 - alpha) * parent.mean_net_edge
uncertainty = math.sqrt(alpha**2 * local_variance / max(effective_observations, 1) + (1-alpha)**2 * parent.uncertainty**2)
lower = min(mean - 1.6448536269514722 * uncertainty, local_lower if local_lower is not None else mean)
```

Persist parent hash, alpha, nominal/effective observations, uncertainty, local/parent/blended means, lower bound, and evidence-through timestamp.

- [x] **Step 6: Blend current soft-regime estimates**

Multiply each regime estimate by the current stored posterior. If one regime is missing, redirect only that probability mass to the non-regime parent with an added uncertainty penalty.

- [x] **Step 7: Run focused tests**

Run: `pytest tests/unit/test_contextual_hierarchy.py -q`

Expected: PASS.

- [x] **Step 8: Commit**

```bash
git add src/contextual/hierarchy.py tests/unit/test_contextual_hierarchy.py
git commit -m "feat: shrink strategy evidence across asset contexts"
```

---

### Task 5: Shrinkage covariance and constrained contextual weights

**Files:**
- Create: `src/contextual/allocation.py`
- Test: `tests/unit/test_contextual_allocation.py`

**Interfaces:**
- Consumes: `HierarchicalEstimate` objects, synchronized out-of-fold returns, prior/previous weights, strategy families, and `AllocationPolicy`.
- Produces: `CovarianceEvidence`, `ContextualWeight`, `ContextualAllocation`, `estimate_strategy_covariance()`, `allocate_contextual_weights()`.

- [x] **Step 1: Write failing covariance, cap, and cash tests**

```python
def test_duplicate_strategies_do_not_receive_false_diversification() -> None:
    returns = pd.DataFrame({"alpha": series, "clone": series, "diverse": other})
    result = allocate_contextual_weights(estimates, returns, prior, {}, families, POLICY, AS_OF)
    assert result.weights["alpha"] + result.weights["clone"] <= result.weights["diverse"] + 0.05
    assert result.effective_strategy_count >= POLICY.minimum_effective_strategies


def test_nonpositive_lower_edges_allocate_all_mass_to_cash() -> None:
    result = allocate_contextual_weights(nonpositive_estimates, returns, prior, {}, families, POLICY, AS_OF)
    assert result.cash_weight == 1.0
    assert all(value == 0 for value in result.weights.values())
```

- [x] **Step 2: Run tests and verify failure**

Run: `pytest tests/unit/test_contextual_allocation.py -q`

- [x] **Step 3: Implement aligned Ledoit-Wolf covariance**

Sort timestamps and strategy IDs, require finite synchronized rows, use `LedoitWolf(assume_centered=False)`, symmetrize the result, clip tiny negative eigenvalues to zero, and hash the exact timestamp/column alignment. Fewer than the configured overlap rows returns `status="insufficient"` and cannot allocate risk.

- [x] **Step 4: Implement deterministic SLSQP allocation**

Use sorted strategy order, a zero vector start blended with the feasible hierarchical prior, fixed `ftol=1e-12`, `maxiter=2_000`, and constraints for total risk mass, strategy caps, and family caps. Objective:

```python
return (
    -float(mu @ weights)
    + policy.risk_penalty * float(weights @ covariance @ weights)
    + policy.turnover_penalty * float(np.square(weights - previous).sum())
    + policy.prior_penalty * float(np.square(weights - prior).sum())
)
```

- [x] **Step 5: Independently validate and canonicalize**

Reject unsuccessful/non-finite results, cap violations, negative mass, total mass above one, ineligible strategy mass, or effective count below policy. On any rejection return a signed `all_cash` allocation. Round canonical persisted weights to 15 significant decimal digits and recompute cash as `1 - sum(weights)`.

- [x] **Step 6: Run focused tests**

Run: `pytest tests/unit/test_contextual_allocation.py tests/unit/test_strategy_ensemble.py -q`

Expected: PASS and no regression in the existing ensemble.

- [x] **Step 7: Commit**

```bash
git add src/contextual/allocation.py tests/unit/test_contextual_allocation.py
git commit -m "feat: allocate covariance-aware strategy weights"
```

---

### Task 6: Portfolio-compatible opportunity selection

**Files:**
- Create: `src/contextual/portfolio.py`
- Test: `tests/unit/test_contextual_portfolio.py`

**Interfaces:**
- Consumes: `ResearchOpportunity`, synchronized asset returns/covariance, `PortfolioSelectionPolicy`, current exposures.
- Produces: `ResearchSizeEvidence`, `PortfolioSelection`, `research_size_ceiling()`, `select_portfolio_opportunities()`.

- [x] **Step 1: Write failing conflict, correlation, and zero-opportunity tests**

```python
def test_selector_keeps_distinct_edge_and_rejects_correlated_duplicate() -> None:
    result = select_portfolio_opportunities((aapl, msft, btc), covariance, POLICY, AS_OF)
    assert result.selected_symbols == ("AAPL", "BTCUSDT")
    assert result.exclusions["MSFT"] == ("correlation_cluster_limit",)


def test_selector_is_allowed_to_hold_only_cash() -> None:
    result = select_portfolio_opportunities((replace(aapl, lower_net_edge=-0.001),), covariance, POLICY, AS_OF)
    assert result.selected == ()
    assert result.cash_weight == 1.0
```

- [x] **Step 2: Run tests and verify failure**

Run: `pytest tests/unit/test_contextual_portfolio.py -q`

- [x] **Step 3: Implement conservative ranking and preselection**

Filter non-positive lower edge, ineligible contexts, conflicting symbol directions, capacity below minimum size, and stale decisions. Sort by lower net edge, then liquidity quality, calibrated probability lower bound, timestamp, and decision hash. Keep at most the configured candidate limit before optimization.

- [x] **Step 4: Implement constrained portfolio weights**

Use positive magnitude variables with signed direction exposure. Enforce gross/net, asset, asset-class, sector, correlation-cluster, capacity, current-risk, and maximum-opportunity constraints. The covariance objective uses the same deterministic validation rules as Task 5. Any infeasibility returns cash.

- [x] **Step 5: Implement size ceilings**

```python
kelly = max((probability_lower * payoff_lower - (1 - probability_lower)) / payoff_lower, 0.0)
ceiling = min(volatility_target, liquidity_capacity, remaining_risk, policy.kelly_fraction * kelly, hard_asset_cap)
```

Reject invalid payoff/probability evidence rather than substituting an optimistic Kelly value.

- [x] **Step 6: Run focused tests**

Run: `pytest tests/unit/test_contextual_portfolio.py tests/unit/test_portfolio.py tests/unit/test_intraday_backtest.py -q`

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add src/contextual/portfolio.py tests/unit/test_contextual_portfolio.py
git commit -m "feat: select portfolio-compatible research opportunities"
```

---

### Task 7: Append-only contextual persistence and migration

**Files:**
- Create: `src/contextual/repository.py`
- Modify: `src/database/schema.py:586-870,1269-1360`
- Modify: `src/database/engine.py:15-95`
- Test: `tests/integration/test_contextual_repository.py`
- Modify: `tests/integration/test_strategy_schema.py`

**Interfaces:**
- Consumes: eligibility, posterior, estimate, covariance, weight, portfolio, trial, and drift evidence objects.
- Produces: `ContextualRepository` append methods and schema-v13 natural identities.

- [x] **Step 1: Write failing schema and tamper/idempotency tests**

```python
EXPECTED = {
    "contextual_outcomes",
    "asset_eligibility_evidence",
    "regime_posteriors",
    "contextual_estimates",
    "contextual_covariances",
    "contextual_weights",
    "portfolio_research_decisions",
    "contextual_learning_trials",
    "contextual_drift_events",
}


def test_contextual_repository_is_append_only_and_idempotent(database) -> None:
    repository = ContextualRepository(database)
    assert repository.append_eligibility(EVIDENCE) == 1
    assert repository.append_eligibility(EVIDENCE) == 0
    with pytest.raises(ValueError, match="hash"):
        repository.append_eligibility(replace(EVIDENCE, quality_score=0.99))
```

- [x] **Step 2: Run tests and verify failure**

Run: `pytest tests/integration/test_contextual_repository.py tests/integration/test_strategy_schema.py -q`

- [x] **Step 3: Add additive schema-v13 tables and natural keys**

Each table stores a primary identity hash, complete context columns used for filtering, `effective_at` or decision/outcome timestamps, bounded JSON evidence, source/version, and `created_at`. Add nonnegative/probability checks where DuckDB supports them. Do not alter or delete legacy ensemble rows.

- [x] **Step 4: Implement canonical row builders and collision checks**

Before treating an existing identity as idempotent, load its canonical evidence hash. Equal identity plus unequal content raises `ValueError`; exact content returns zero inserted rows.

- [x] **Step 5: Bump and verify schema version**

Set `SCHEMA_VERSION = 13`. Fresh and schema-v12 databases must initialize twice without data loss and record one schema-v13 row.

- [x] **Step 6: Run focused tests**

Run: `pytest tests/integration/test_contextual_repository.py tests/integration/test_strategy_schema.py tests/integration/test_strategy_engine.py -q`

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add src/contextual/repository.py src/database/schema.py src/database/engine.py tests/integration/test_contextual_repository.py tests/integration/test_strategy_schema.py
git commit -m "feat: persist contextual research evidence"
```

---

### Task 8: Publish authenticated strategy outcomes and causal context

**Files:**
- Modify: `src/strategies/pipeline.py:486-570,1573-1765,1880-1990`
- Create: `tests/integration/test_contextual_strategy_pipeline.py`
- Extend: `tests/unit/test_strategy_no_repaint.py`

**Interfaces:**
- Consumes: `EvaluationBatch.resolved_outcomes`, causal bars, instrument/profile configuration.
- Produces: append-only `contextual_outcomes` and decision-time regime/eligibility evidence for every resolved component outcome.

- [x] **Step 1: Write failing end-to-end outcome publication test**

```python
def test_strategy_evaluation_publishes_contextual_outcomes_without_final_rows(project_root) -> None:
    outcome = pipeline.evaluate(options)
    rows = database.frame("select * from contextual_outcomes order by outcome_available_at")
    assert outcome.status == "completed"
    assert not rows.empty
    assert (pd.to_datetime(rows.outcome_available_at, utc=True) < SEALED_FINAL_START).all()
    assert set(rows.direction) <= {"long", "short"}
    assert rows.evidence.map(lambda item: item["source_decision_hash"]).notna().all()
```

- [x] **Step 2: Run tests and verify no rows failure**

Run: `pytest tests/integration/test_contextual_strategy_pipeline.py tests/unit/test_strategy_no_repaint.py -q`

- [x] **Step 3: Add contextual evidence to `EvaluationBatch`**

During `_evaluate_engines`, build one causal regime-posterior frame from the sealed bar snapshot, evaluate bar-proxy eligibility at each resolved decision, and enrich resolved outcomes with profile, asset class, direction, four stored posterior probabilities, gross return, modeled cost, and net return. Do not include final-boundary executions.

- [x] **Step 4: Persist in the existing atomic cohort transaction**

Use `ContextualRepository.row_for_outcome()` but insert through the same SQLAlchemy connection as strategy runs and ensemble weights. A source-generation race must commit neither legacy nor contextual cohort rows.

- [x] **Step 5: Extend cohort completeness validation**

For each persisted component, verify contextual outcome count and hashes match the in-memory batch. Cached evaluation reuse must require the contextual cohort to be complete once schema-v13 evidence exists.

- [x] **Step 6: Run focused and regression tests**

Run: `pytest tests/integration/test_contextual_strategy_pipeline.py tests/integration/test_strategy_engine.py tests/integration/test_strategy_cli.py tests/unit/test_strategy_no_repaint.py -q`

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add src/strategies/pipeline.py tests/integration/test_contextual_strategy_pipeline.py tests/unit/test_strategy_no_repaint.py
git commit -m "feat: publish causal strategy context outcomes"
```

---

### Task 9: Contextual research service and CLI workflows

**Files:**
- Create: `src/contextual/service.py`
- Modify: `src/contextual/__init__.py`
- Modify: `src/cli.py:43-50,409-615`
- Test: `tests/integration/test_contextual_service.py`
- Test: `tests/integration/test_contextual_cli.py`

**Interfaces:**
- Produces: `ContextualRunRequest`, `ContextualRunResult`, `ContextualResearchService.screen_universe()`, `.evaluate_contexts()`, `.backtest_portfolio()`, `.learn_contextual()`.
- CLI: `strategy screen-universe`, `strategy evaluate-contexts`, `strategy backtest-portfolio`, `strategy learn-contextual`.

- [x] **Step 1: Write failing service and CLI tests**

```python
def test_evaluate_contexts_emits_ordered_stages_and_persists_cash_safe_result(service) -> None:
    events = []
    result = service.evaluate_contexts(REQUEST, events.append)
    assert [event.stage for event in events] == [
        "eligibility", "regimes", "hierarchy", "covariance", "allocation", "portfolio"
    ]
    assert result.portfolio.cash_weight >= 0
    assert database.scalar("select count(*) from contextual_weights") > 0


def test_screen_universe_cli_returns_json_progress(runner) -> None:
    result = runner.invoke(app, ["strategy", "screen-universe", "--symbols", "BTCUSDT,ETHUSDT"])
    assert result.exit_code == 0
    assert '"stage": "eligibility"' in result.stdout
```

- [x] **Step 2: Run tests and verify failure**

Run: `pytest tests/integration/test_contextual_service.py tests/integration/test_contextual_cli.py -q`

- [x] **Step 3: Implement deterministic database assembly**

Resolve configured instrument/profile identity, latest complete dataset cohorts, contextual outcomes available by `as_of`, current finalized bars, latest authenticated quote/depth if present, current strategy evaluations, and previous contextual weights. Refuse mixed protocol/dataset contexts and report exact missing prerequisites.

- [x] **Step 4: Implement service stages**

`screen_universe` persists eligibility and posterior evidence. `evaluate_contexts` builds hierarchy/covariance/weights then portfolio selection. `backtest_portfolio` repeats those operations inside each outer fold and writes existing backtest-run/curve/sensitivity records with a contextual protocol hash. `learn_contextual` delegates to Task 11's bounded search.

- [x] **Step 5: Add four Typer commands**

Commands accept comma-separated bounded symbols, provider/feed, interval, mode, explicit UTC `as_of`, database URL, finite budget/seed for learning, and optional CSV source. They use existing newline-delimited `PipelineEvent` formatting and return nonzero for unavailable prerequisites.

- [x] **Step 6: Run focused tests**

Run: `pytest tests/integration/test_contextual_service.py tests/integration/test_contextual_cli.py tests/integration/test_strategy_cli.py -q`

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add src/contextual/service.py src/contextual/__init__.py src/cli.py tests/integration/test_contextual_service.py tests/integration/test_contextual_cli.py
git commit -m "feat: orchestrate contextual market research"
```

---

### Task 10: Context-authenticated online replay and live alert gates

**Files:**
- Create: `src/contextual/online.py`
- Modify: `src/live_monitor/engine.py:35-190`
- Modify: `src/live_monitor/evidence.py`
- Modify: `src/live_monitor/repository.py`
- Test: `tests/unit/test_contextual_online.py`
- Extend: `tests/unit/test_live_monitor_evidence.py`
- Extend: `tests/integration/test_live_monitor_repository.py`

**Interfaces:**
- Produces: `attribute_soft_regime_outcome()`, `replay_contextual_outcomes()`, `ContextualOnlineState`.
- Extends: `EligibilityEvidence` with profile, eligibility/context hashes, regime probabilities, contextual drift, and portfolio-selection identity.

- [x] **Step 1: Write failing replay and live-gate tests**

```python
def test_soft_regime_credit_is_conserved_and_replay_idempotent() -> None:
    attributed = attribute_soft_regime_outcome(OUTCOME, POSTERIOR)
    assert sum(item.credit for item in attributed) == pytest.approx(1.0)
    assert replay_contextual_outcomes(BASE, attributed) == replay_contextual_outcomes(BASE, attributed * 2)


def test_live_alert_requires_eligible_context_and_portfolio_selection() -> None:
    decision = evaluate_alert_eligibility(
        eligible_live_evidence(portfolio_selected=False), QUOTE, health=MonitorHealth.HEALTHY, now=NOW
    )
    assert decision.status == "abstain"
    assert "portfolio_selection_required" in decision.reasons
```

- [x] **Step 2: Run tests and verify failure**

Run: `pytest tests/unit/test_contextual_online.py tests/unit/test_live_monitor_evidence.py -q`

- [x] **Step 3: Implement exact-context soft attribution**

Create four regime-credit records from the posterior stored on the original decision. Authenticate outcome identity, context hash, decision hash, outcome watermark, and normalized probabilities. Deduplicate by outcome ID before applying the existing adaptive fixed-share loss update within each cell.

- [x] **Step 4: Shrink online cells to frozen parent priors**

After replay, combine online and parent weights with effective-sample alpha from Task 4, reapply Task 5 caps/covariance validation, and persist a state hash containing processed outcome IDs, weights, parent hash, learning-rate trace, and watermark.

- [x] **Step 5: Extend live eligibility gates**

Require `eligibility_state == "eligible"`, matching policy/context/cohort hashes, non-stressed contextual drift, authenticated covariance/weight evidence, and `portfolio_selected`. A legacy evidence payload remains decodable but abstains with `contextual_evidence_required`.

- [x] **Step 6: Run focused and monitor regression tests**

Run: `pytest tests/unit/test_contextual_online.py tests/unit/test_live_monitor_evidence.py tests/unit/test_live_monitor_levels.py tests/integration/test_live_monitor_repository.py tests/integration/test_live_monitor_startup.py -q`

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add src/contextual/online.py src/live_monitor/engine.py src/live_monitor/evidence.py src/live_monitor/repository.py tests/unit/test_contextual_online.py tests/unit/test_live_monitor_evidence.py tests/integration/test_live_monitor_repository.py
git commit -m "feat: gate live alerts with contextual evidence"
```

---

### Task 11: Bounded contextual self-improvement and trial accounting

**Files:**
- Modify: `src/learning/search.py:25-260,400-675`
- Modify: `src/deep_research/contracts.py`
- Modify: `src/contextual/service.py`
- Test: `tests/unit/test_contextual_learning.py`
- Extend: `tests/unit/test_learning_search.py`
- Extend: `tests/integration/test_deep_research_end_to_end.py`

**Interfaces:**
- Produces: `ContextualCandidate`, `ContextualSearchSpace`, `generate_contextual_candidates()`, `evaluate_contextual_candidate()`.
- Persists: globally unique attempted contextual policies in `contextual_learning_trials`.

- [x] **Step 1: Write failing bounded-search and global-identity tests**

```python
def test_each_contextual_degree_of_freedom_changes_global_trial_identity() -> None:
    base = ContextualCandidate.defaults()
    hashes = {
        candidate.global_trial_id(DATASET, PROTOCOL)
        for candidate in (
            base,
            replace(base, prior_strength=base.prior_strength + 10),
            replace(base, risk_penalty=base.risk_penalty * 2),
            replace(base, minimum_liquidity_quality=0.9),
        )
    }
    assert len(hashes) == 4


def test_contextual_search_never_reads_sealed_rows() -> None:
    with pytest.raises(ValueError, match="sealed"):
        evaluate_contextual_candidate(CANDIDATE, frame_with_sealed_rows(), EXPERIMENT)
```

- [x] **Step 2: Run tests and verify failure**

Run: `pytest tests/unit/test_contextual_learning.py tests/unit/test_learning_search.py -q`

- [x] **Step 3: Define a closed contextual search space**

Search only explicit finite grids for profile threshold multipliers, direction-specific holding horizon, regime uncertainty penalty, hierarchy prior strengths, covariance risk penalty, turnover penalty, prior penalty, minimum lower edge, correlation cap, and Kelly fraction. Validate hard safe bounds and prohibit new symbols, code, expressions, regime names, broker operations, or promotion thresholds.

- [x] **Step 4: Implement deterministic candidates and nested fitness**

Generate baseline, one-at-a-time neighbors, seeded combinations, then successive-halving survivors. Fitness is median outer-fold net Sharpe minus drawdown, turnover, instability, context-fragmentation, complexity, and concentration penalties. Every generated/duplicate/failed/interrupted candidate is persisted before evaluation with a global trial identity.

- [x] **Step 5: Enforce champion/challenger states**

The best development candidate remains `shadow`; only the existing sealed test and promotion services can advance it. A new candidate hash always creates a new forward cohort and cannot inherit readiness.

- [x] **Step 6: Run focused and deep-research regression tests**

Run: `pytest tests/unit/test_contextual_learning.py tests/unit/test_learning_search.py tests/integration/test_deep_research_end_to_end.py tests/integration/test_learning_mode.py -q`

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add src/learning/search.py src/deep_research/contracts.py src/contextual/service.py tests/unit/test_contextual_learning.py tests/unit/test_learning_search.py tests/integration/test_deep_research_end_to_end.py
git commit -m "feat: search contextual policies safely"
```

---

### Task 12: Backward-compatible contextual app snapshot

**Files:**
- Modify: `src/app_snapshot/models.py:105-185,269-320,515-555`
- Modify: `src/app_snapshot/builder.py:259-340,496-620,720-955,1478-1535`
- Extend: `tests/unit/test_app_snapshot.py`
- Extend: `tests/integration/test_app_snapshot_export.py`

**Interfaces:**
- Produces optional contextual fields on `InstrumentSnapshot`, `ResearchSignalSnapshot`, `StrategySnapshot`, and `EnsembleComponentSnapshot` without changing `AppSnapshot.schema_version == 5`.

- [x] **Step 1: Write failing projection and legacy-compatibility tests**

```python
def test_snapshot_projects_latest_complete_contextual_cohort(database, settings) -> None:
    snapshot = build_app_snapshot(database, settings)
    btc = next(item for item in snapshot.instruments if item.symbol == "BTCUSDT")
    assert btc.asset_profile == "crypto_major_spot"
    assert btc.eligibility_state in {"eligible", "watch", "blocked"}
    assert sum(btc.regime_probabilities.values()) == pytest.approx(1.0)


def test_schema_v5_model_accepts_missing_contextual_fields() -> None:
    snapshot = AppSnapshot.model_validate(LEGACY_SCHEMA_V5)
    assert snapshot.instruments[0].eligibility_state is None
```

- [x] **Step 2: Run tests and verify missing fields fail**

Run: `pytest tests/unit/test_app_snapshot.py tests/integration/test_app_snapshot_export.py -q`

- [x] **Step 3: Add optional bounded snapshot fields**

Add profile, eligibility state/reasons/quality/hash, spread/depth/impact/capacity/coverage, four regime probabilities, posterior uncertainty, local/parent/final weight, effective observations/strategy count, covariance status, portfolio rank/selected/size ceiling/conflicts, and contextual drift. Validate finite ranges and normalized probability maps when present.

- [x] **Step 4: Project only complete authenticated cohorts**

Select latest records whose content/context/cohort hashes agree. A partial or mismatched cohort contributes no contextual fields; it cannot override legacy signal/ensemble evidence. Bound lists and JSON exactly as existing snapshot defenses do.

- [x] **Step 5: Run focused tests**

Run: `pytest tests/unit/test_app_snapshot.py tests/integration/test_app_snapshot_export.py tests/integration/test_native_snapshot_demo.py -q`

Expected: PASS.

- [x] **Step 6: Commit**

```bash
git add src/app_snapshot/models.py src/app_snapshot/builder.py tests/unit/test_app_snapshot.py tests/integration/test_app_snapshot_export.py
git commit -m "feat: export contextual market evidence"
```

---

### Task 13: Native macOS contextual research experience

**Files:**
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Models/Snapshot.swift:260-410,510-570`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Models/EngineJob.swift:150-380`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Features/Markets/MarketsView.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Features/Markets/InstrumentDetailView.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Features/Signals/SignalDetailView.swift`
- Modify: `macos/Nowcaster/Sources/NowcasterApp/Features/StrategyLab/StrategyLabView.swift`
- Extend: `macos/Nowcaster/Tests/NowcasterAppTests/SnapshotDecodingTests.swift`
- Extend: `macos/Nowcaster/Tests/NowcasterAppTests/EngineRunnerTests.swift`
- Create: `macos/Nowcaster/Tests/NowcasterAppTests/ContextualResearchPresentationTests.swift`

**Interfaces:**
- Consumes: Task 12 optional fields and Task 9 CLI.
- Produces: `EngineJob.contextualResearch`, eligibility/regime presentation, “Why this asset now,” and contextual Strategy Lab action.

- [x] **Step 1: Write failing Swift decoding, invocation, and presentation tests**

```swift
@Test func legacySnapshotKeepsContextualEvidenceOptional() throws {
    let snapshot = try JSONDecoder.nowcaster.decode(NowcasterSnapshot.self, from: legacyV5)
    #expect(snapshot.instruments[0].eligibilityState == nil)
}

@Test func contextualJobBuildsBoundedUniverseInvocation() throws {
    let invocation = try EngineJob.contextualResearch(symbols: ["BTCUSDT", "ETHUSDT"], asset: context)
        .invocation(configuration: configuration)
    #expect(invocation.arguments.contains("evaluate-contexts"))
    #expect(invocation.arguments.contains("BTCUSDT,ETHUSDT"))
}
```

- [x] **Step 2: Run Swift tests and verify failure**

Run: `cd macos/Nowcaster && swift test --filter Contextual`

- [x] **Step 3: Decode bounded optional evidence safely**

Add optional fields using existing unknown-enum and decoding-budget patterns. Expose presentation helpers that normalize reason codes, explain probability/edge/cost separately, and never turn missing context into an eligible state.

- [x] **Step 4: Add native market and signal evidence**

Markets shows compact Eligibility and Regime columns when width permits. Instrument detail adds Asset selection and Regime probability `GroupBox` sections. Signal detail adds **Why this asset now**, local-versus-parent influence, portfolio selection/conflict, and the research-size ceiling disclaimer.

- [x] **Step 5: Add Strategy Lab contextual action**

Add a system-standard button with accessibility identifier `strategyLab.contextualResearch`. It invokes the bounded configured instrument set, streams existing progress events, follows with snapshot export, remains disabled while another engine job runs, and never implies broker execution.

- [x] **Step 6: Run Swift tests and build**

Run: `cd macos/Nowcaster && swift test`

Run: `cd macos/Nowcaster && swift build -c release`

Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add macos/Nowcaster/Sources macos/Nowcaster/Tests
git commit -m "feat: explain contextual opportunities on macOS"
```

---

### Task 14: Documentation, end-to-end audit, and release verification

**Files:**
- Modify: `README.md`
- Modify: `docs/strategy-methodology.md`
- Modify: `docs/live-monitor.md`
- Modify: `docs/backtest_protocol.md`
- Extend: `tests/integration/test_contextual_service.py`
- Extend: `tests/integration/test_full_strategy_research.py`

**Interfaces:**
- Validates all prior tasks as one causal, backward-compatible release.

- [x] **Step 1: Write the failing end-to-end acceptance test**

Create a deterministic two-asset fixture with a trending liquid asset and a correlated duplicate candidate. Run screen → regime → hierarchy → strategy allocation → portfolio selection → snapshot. Assert the chosen asset/strategy is supported by out-of-fold evidence, the duplicate is penalized, a future-tail mutation leaves every prior hash unchanged, and the snapshot explains both selection and exclusion.

- [x] **Step 2: Run the acceptance test and verify failure before final wiring**

Run: `pytest tests/integration/test_contextual_service.py::test_full_contextual_pipeline_is_causal_and_explainable -q`

- [x] **Step 3: Wire the acceptance path through its declared boundaries**

`ContextualResearchService.evaluate_contexts()` must append the portfolio decision before returning. `build_app_snapshot()` must call `_contextual_projection(database)` once, join instruments by symbol and signals by exact decision/context hash, and apply only a complete cohort. The acceptance test must invoke those public boundaries; it must not call eligibility, regime, allocation, or snapshot helper functions directly. Do not weaken thresholds, replace unavailable evidence, or special-case the fixture.

- [x] **Step 4: Update beginner-readable documentation**

README must explain: why the app may ignore most assets; how strategies specialize; why correlated indicators do not count as independent votes; what regimes mean; why “abstain” is often the safest result; what backtests can and cannot establish; and that notifications/research-size ceilings do not place orders or guarantee profit. Methodology documents must list exact causal, statistical, cost, and trial-accounting contracts.

- [x] **Step 5: Run targeted contextual and safety suites**

Run:

```bash
pytest tests/unit/test_contextual_config.py tests/unit/test_contextual_eligibility.py \
  tests/unit/test_contextual_regimes.py tests/unit/test_contextual_hierarchy.py \
  tests/unit/test_contextual_allocation.py tests/unit/test_contextual_portfolio.py \
  tests/unit/test_contextual_online.py tests/unit/test_contextual_learning.py \
  tests/integration/test_contextual_repository.py tests/integration/test_contextual_strategy_pipeline.py \
  tests/integration/test_contextual_service.py tests/integration/test_contextual_cli.py -q
```

Expected: PASS.

- [x] **Step 6: Run complete Python quality gates**

Run:

```bash
python -m ruff format --check src tests
python -m ruff check src tests
pytest -q
```

Expected: all checks PASS.

- [x] **Step 7: Run complete native gates**

Run:

```bash
cd macos/Nowcaster
swift test
swift build -c release
```

Expected: PASS.

- [x] **Step 8: Run repository release and safety scripts**

Run:

```bash
make research-ci
make verify-swift-fixture-parity
make replay-live-monitor
make verify-live-monitor
make secret-scan
make engine-bundle
make macos-app
```

Expected: every target PASS and `build/Nowcaster.app` exists. Run `./scripts/verify_production_release.sh build/Nowcaster.app` only when a Developer ID/notarized build is configured; otherwise record that external signing/notarization was unavailable and do not claim that production-signing gate.

- [x] **Step 9: Review the diff for scope and secrets**

Run:

```bash
git status --short
git diff --check
git diff --stat HEAD~14..HEAD
git grep -n -I -E '(API_KEY|SECRET_KEY|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY)' -- ':!docs/superpowers/plans/*'
```

Expected: only intended files, no whitespace errors, and no credentials.

- [x] **Step 10: Commit documentation and final wiring**

```bash
git add README.md docs tests/integration/test_contextual_service.py tests/integration/test_full_strategy_research.py
git commit -m "docs: explain contextual strategy research"
```

- [x] **Step 11: Push verified commits**

Run: `git push origin main`

Expected: remote `main` resolves to the verified local HEAD.
