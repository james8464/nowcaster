# Alternative-Data Earnings Nowcaster Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible point-in-time earnings-nowcasting research platform from public data, with tested forecasting, variant signals, event studies, a six-page Streamlit dashboard, and recruiter-ready artifacts.

**Architecture:** A stage-oriented CLI moves source-labelled snapshots through validation, SQLAlchemy/DuckDB persistence, point-in-time feature construction, expanding-window models, consensus/proxy comparison, and event analysis. Dashboard and report layers read persisted outputs only; HTTP providers, repositories, transformations, and presentation stay independently testable.

**Tech Stack:** Python 3.11–3.13, pandas, NumPy, HTTPX, SQLAlchemy, DuckDB, statsmodels, scikit-learn, SciPy, Plotly, Matplotlib, Streamlit, Pydantic, Typer, PyYAML, pytest.

**Spec:** `docs/superpowers/specs/2026-08-22-earnings-nowcaster-design.md`

## Global Constraints

- The core and `make demo` must require no paid API and no Goldman Marquee credentials.
- Demo observations must be real public snapshots with provenance; synthetic records are allowed only in tests.
- Every historical feature must satisfy `available_date <= forecast_cutoff_date`.
- Time-series model evaluation uses expanding or rolling windows, never random train/test splits.
- Actual consensus, manual consensus, and expectation proxies must remain visibly distinct.
- Every reported statistic is derived from persisted successful-run data; missing evidence produces an explicit unavailable state.
- The documented installation path must work on macOS with Python 3.11–3.13.
- The existing parent `gs_quant` source must remain unmodified.

---

### Task 1: Foundation, configuration, logging, and database schema

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `Makefile`
- Create: `config/universe.yaml`, `config/features.yaml`, `config/model.yaml`
- Create: `src/__init__.py`, `src/config/__init__.py`, `src/config/settings.py`
- Create: `src/utils/__init__.py`, `src/utils/logging.py`, `src/utils/provenance.py`
- Create: `src/database/__init__.py`, `src/database/schema.py`, `src/database/engine.py`, `src/database/repositories.py`
- Create: `src/cli.py`
- Test: `tests/unit/test_config.py`, `tests/unit/test_database.py`, `tests/unit/test_provenance.py`

**Interfaces:**
- Produces: `Settings.load(project_root: Path | None) -> Settings`
- Produces: `Database.from_url(url: str) -> Database`, `Database.initialize() -> None`, `Database.upsert(table_name: str, rows: Sequence[Mapping]) -> int`
- Produces: `capture_run_context(command: str, config_hash: str) -> RunContext`

- [x] **Step 1: Write failing configuration and database tests**

```python
def test_settings_loads_yaml_and_env(project_root, monkeypatch):
    monkeypatch.setenv("NOWCASTER_MODE", "demo")
    settings = Settings.load(project_root)
    assert settings.mode == "demo"
    assert 7 in settings.model.forecast_horizons


def test_financial_unique_key_is_enforced(tmp_path):
    db = Database.from_url(f"duckdb:///{tmp_path / 'test.duckdb'}")
    db.initialize()
    assert db.upsert("companies", [company_row]) == 1
    assert db.upsert("companies", [company_row]) == 0
```

- [x] **Step 2: Run tests and verify missing-module failures**

Run: `pytest tests/unit/test_config.py tests/unit/test_database.py tests/unit/test_provenance.py -q`

- [x] **Step 3: Implement packaging, typed settings, structured logging, provenance, schema, repositories, and CLI `init-db`**

```python
class Database:
    @classmethod
    def from_url(cls, url: str) -> "Database":
        return cls(create_engine(url, future=True))

    def initialize(self) -> None:
        metadata.create_all(self.engine)

    def upsert(self, table_name: str, rows: Sequence[Mapping[str, Any]]) -> int:
        table = TABLES[table_name]
        inserted = deduplicate_against_database(self.engine, table, rows)
        with self.engine.begin() as connection:
            connection.execute(insert(table), inserted)
        return len(inserted)

    def frame(self, statement: Select | str, params: Mapping[str, Any] | None = None) -> pd.DataFrame:
        return pd.read_sql(statement, self.engine, params=params)
```

- [x] **Step 4: Run foundation tests and CLI smoke check**

Run: `pytest tests/unit/test_config.py tests/unit/test_database.py tests/unit/test_provenance.py -q && python -m src.cli init-db --database-url duckdb:///data/test.duckdb`

- [x] **Step 5: Commit foundation**

```bash
git add pyproject.toml .gitignore .env.example Makefile config src tests
git commit -m "feat: establish nowcaster foundation"
```

### Task 2: HTTP cache, SEC ingestion, and fundamental normalization

**Files:**
- Create: `src/ingestion/__init__.py`, `src/ingestion/http.py`, `src/ingestion/sec.py`
- Create: `src/validation/__init__.py`, `src/validation/fundamentals.py`, `src/validation/report.py`
- Create: `data/demo/sec/*.json`, `data/demo/manifest.json`
- Test: `tests/fixtures/sec/*.json`, `tests/unit/test_sec.py`, `tests/unit/test_fundamental_validation.py`, `tests/integration/test_sec_pipeline.py`

**Interfaces:**
- Consumes: `Settings`, `Database`
- Produces: `CachedHttpClient.get_json(url: str, cache_key: str) -> dict`
- Produces: `SecClient.company_facts(cik: str) -> dict`, `SecClient.submissions(cik: str) -> dict`
- Produces: `normalize_company_facts(payload: Mapping, company: CompanyConfig) -> list[QuarterlyFinancial]`
- Produces: `validate_financials(frame: pd.DataFrame) -> list[QualityIssue]`

- [x] **Step 1: Write failing SEC parser and normalization tests**

```python
def test_normalizer_prefers_standard_revenue_and_standalone_quarter(sec_companyfacts):
    rows = normalize_company_facts(sec_companyfacts, company_config)
    q2 = next(row for row in rows if row.fiscal_quarter == "2024Q2")
    assert q2.revenue == Decimal("9350000000")
    assert q2.selected_tag == "RevenueFromContractWithCustomerExcludingAssessedTax"
    assert q2.available_date == date(2024, 8, 7)


def test_ytd_facts_are_differenced_only_when_components_tie(sec_ytd_facts):
    rows = normalize_company_facts(sec_ytd_facts, company_config)
    assert quarter(rows, "2024Q2").revenue == Decimal("240")
```

- [x] **Step 2: Verify tests fail before implementation**

Run: `pytest tests/unit/test_sec.py tests/unit/test_fundamental_validation.py -q`

- [x] **Step 3: Implement compliant cached HTTP and SEC normalization with issuer overrides**

```python
class SecClient:
    def __init__(self, http: CachedHttpClient, user_agent: str, requests_per_second: float = 2.0):
        if "@" not in user_agent:
            raise ValueError("SEC_USER_AGENT must include a contact email")
        self.http, self.user_agent = http, user_agent
        self.limiter = RateLimiter(requests_per_second)

    def company_facts(self, cik: str) -> dict[str, Any]:
        self.limiter.wait()
        return self.http.get_json(
            f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json",
            headers={"User-Agent": self.user_agent},
        )

    def submissions(self, cik: str) -> dict[str, Any]:
        self.limiter.wait()
        return self.http.get_json(
            f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json", headers={"User-Agent": self.user_agent}
        )
```

- [x] **Step 4: Add data-quality rules and persisted issue report**

Run: `pytest tests/unit/test_sec.py tests/unit/test_fundamental_validation.py tests/integration/test_sec_pipeline.py -q`

- [x] **Step 5: Commit fundamental pipeline**

```bash
git add src/ingestion src/validation data/demo/sec data/demo/manifest.json tests
git commit -m "feat: ingest and normalize SEC fundamentals"
```

### Task 3: Price, earnings-event, and market-return pipeline

**Files:**
- Create: `src/ingestion/prices.py`, `src/ingestion/earnings.py`
- Create: `src/backtest/__init__.py`, `src/backtest/returns.py`
- Create: `data/demo/prices/*.csv`, `data/demo/earnings_calendar.csv`
- Test: `tests/unit/test_prices.py`, `tests/unit/test_event_returns.py`, `tests/integration/test_market_pipeline.py`

**Interfaces:**
- Produces: `PriceProvider.fetch(symbol: str, start: date, end: date) -> pd.DataFrame`
- Produces: `CsvPriceProvider`, `StooqPriceProvider`, `AlphaVantagePriceProvider`
- Produces: `calculate_event_return(prices, event_date, window, benchmark=None) -> EventReturn`

- [x] **Step 1: Write failing provider-normalization and event-window tests**

```python
def test_event_return_uses_trading_rows_and_adjusted_close(price_frame):
    result = calculate_event_return(price_frame, date(2024, 2, 3), (0, 3))
    assert result.start_date == date(2024, 2, 5)
    assert result.end_date == date(2024, 2, 8)
    assert result.raw_return == pytest.approx(0.08)


def test_abnormal_return_subtracts_matched_benchmark(company_prices, spy_prices):
    result = calculate_event_return(company_prices, event_date, (0, 1), spy_prices)
    assert result.abnormal_return == pytest.approx(result.raw_return - result.benchmark_return)
```

- [x] **Step 2: Verify failures**

Run: `pytest tests/unit/test_prices.py tests/unit/test_event_returns.py -q`

- [x] **Step 3: Implement providers, strict adjusted-price metadata, earnings-calendar CSV, and event returns**

```python
class PriceProvider(Protocol):
    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        raise NotImplementedError
```

- [x] **Step 4: Cross-check returns against GS Quant when importable and run market integration tests**

Run: `pytest tests/unit/test_prices.py tests/unit/test_event_returns.py tests/integration/test_market_pipeline.py -q`

- [x] **Step 5: Commit market pipeline**

```bash
git add src/ingestion/prices.py src/ingestion/earnings.py src/backtest data/demo/prices data/demo/earnings_calendar.csv tests
git commit -m "feat: add market and earnings-event data"
```

### Task 4: Alternative attention and macro providers

**Files:**
- Create: `src/ingestion/wikipedia.py`, `src/ingestion/macro.py`, `src/ingestion/trends.py`
- Create: `data/demo/alternative/*.csv`, `data/demo/macro/*.csv`
- Test: `tests/unit/test_wikipedia.py`, `tests/unit/test_macro.py`, `tests/integration/test_altdata_pipeline.py`

**Interfaces:**
- Produces: `WikipediaProvider.fetch(company, start, end) -> list[AlternativeObservation]`
- Produces: `FredProvider.fetch(series_id, start, end, vintage_dates) -> list[MacroObservation]`
- Produces: `ManualTrendsProvider.fetch(company, start, end) -> list[AlternativeObservation]`

- [ ] **Step 1: Write failing availability, provider-shape, and missing-data tests**

```python
def test_wikipedia_applies_conservative_availability_lag(wiki_response):
    rows = parse_pageviews(wiki_response, company_id="SBUX", availability_lag_days=1)
    assert rows[0].available_date == rows[0].observation_date + timedelta(days=1)


def test_fred_vintage_is_preserved(fred_response):
    rows = parse_fred(fred_response, "RSAFS")
    assert rows[0].vintage_date == date(2020, 4, 15)
```

- [ ] **Step 2: Verify failures**

Run: `pytest tests/unit/test_wikipedia.py tests/unit/test_macro.py -q`

- [ ] **Step 3: Implement Wikimedia, FRED/ALFRED, bundled CSV, and disabled-by-default search adapters**

```python
class AlternativeDataProvider(Protocol):
    def fetch(self, company: CompanyConfig, start: date, end: date) -> list[AlternativeObservation]:
        raise NotImplementedError
```

- [ ] **Step 4: Run alternative-data integration tests and provenance assertions**

Run: `pytest tests/unit/test_wikipedia.py tests/unit/test_macro.py tests/integration/test_altdata_pipeline.py -q`

- [ ] **Step 5: Commit alternative data**

```bash
git add src/ingestion data/demo/alternative data/demo/macro tests
git commit -m "feat: add point-in-time alternative data"
```

### Task 5: Point-in-time feature store and leakage enforcement

**Files:**
- Create: `src/features/__init__.py`, `src/features/aggregation.py`, `src/features/builder.py`, `src/features/leakage.py`
- Test: `tests/unit/test_feature_aggregation.py`, `tests/unit/test_growth.py`, `tests/unit/test_leakage.py`, `tests/integration/test_feature_store.py`

**Interfaces:**
- Produces: `revenue_yoy_log_growth(financials: pd.DataFrame) -> pd.Series`
- Produces: `aggregate_as_of(observations, cutoff, windows) -> dict[str, float]`
- Produces: `FeatureBuilder.build(company_quarters, horizons) -> pd.DataFrame`
- Produces: `assert_no_lookahead(features: pd.DataFrame) -> None`

- [ ] **Step 1: Write failing point-in-time and future-mutation tests**

```python
def test_feature_builder_excludes_observations_available_after_cutoff(builder, observations):
    frame = builder.build(quarters, horizons=[7])
    assert (frame.maximum_input_available_date <= frame.forecast_cutoff_date).all()


def test_future_observation_does_not_change_historical_features(builder, observations):
    before = builder.build(quarters, horizons=[7])
    observations.loc[len(observations)] = future_observation
    after = builder.build(quarters, horizons=[7])
    pd.testing.assert_frame_equal(before, after)
```

- [ ] **Step 2: Verify failures**

Run: `pytest tests/unit/test_feature_aggregation.py tests/unit/test_growth.py tests/unit/test_leakage.py -q`

- [ ] **Step 3: Implement cutoff-aware fundamentals, attention, macro, seasonal, relative, and missingness features**

```python
def assert_no_lookahead(features: pd.DataFrame) -> None:
    leaked = features[features["maximum_input_available_date"] > features["forecast_cutoff_date"]]
    if not leaked.empty:
        raise LookaheadError(leaked[["company_id", "fiscal_quarter", "feature_name"]])
```

- [ ] **Step 4: Run unit and DuckDB feature-store integration tests**

Run: `pytest tests/unit/test_feature_aggregation.py tests/unit/test_growth.py tests/unit/test_leakage.py tests/integration/test_feature_store.py -q`

- [ ] **Step 5: Commit feature store**

```bash
git add src/features tests
git commit -m "feat: build leakage-safe feature store"
```

### Task 6: Expanding-window baselines, machine learning, ablations, and explanations

**Files:**
- Create: `src/models/__init__.py`, `src/models/base.py`, `src/models/baselines.py`, `src/models/linear.py`, `src/models/tree.py`, `src/models/validation.py`, `src/models/explain.py`, `src/models/metrics.py`
- Test: `tests/unit/test_baselines.py`, `tests/unit/test_model_validation.py`, `tests/unit/test_model_metrics.py`, `tests/integration/test_training_pipeline.py`

**Interfaces:**
- Produces: `ForecastModel.fit(X, y)`, `ForecastModel.predict(X) -> ForecastOutput`
- Produces: `expanding_window_forecasts(matrix, model_specs, minimum_training_quarters, seed) -> tuple[predictions, run_metadata]`
- Produces: `evaluate_forecasts(predictions) -> pd.DataFrame`

- [ ] **Step 1: Write failing seasonal-baseline, fold-boundary, metric, and contribution tests**

```python
def test_expanding_folds_never_train_on_test_or_future_rows(feature_matrix):
    forecasts, runs = expanding_window_forecasts(feature_matrix, specs, 8, 42)
    assert all(run.training_end < run.test_start for run in runs)


def test_linear_contributions_sum_to_prediction_adjustment(fitted_linear, row):
    explanation = explain_linear(fitted_linear, row)
    assert sum(explanation.contributions.values()) + explanation.intercept == pytest.approx(explanation.prediction)
```

- [ ] **Step 2: Verify failures**

Run: `pytest tests/unit/test_baselines.py tests/unit/test_model_validation.py tests/unit/test_model_metrics.py -q`

- [ ] **Step 3: Implement seasonal/history baselines, OLS, Ridge, Elastic Net, gradient boosting, fold-local preprocessing, and sample gates**

```python
def expanding_window_forecasts(
    matrix: pd.DataFrame,
    model_specs: Sequence[ModelSpec],
    minimum_training_quarters: int,
    seed: int,
) -> tuple[pd.DataFrame, list[ModelRunRecord]]:
    predictions, runs = [], []
    for fold in make_expanding_folds(matrix, minimum_training_quarters):
        for spec in model_specs:
            fitted = build_model_pipeline(spec, seed).fit(fold.X_train, fold.y_train)
            predictions.extend(predict_fold(fitted, fold, spec))
            runs.append(record_model_run(fitted, fold, spec, seed))
    return pd.DataFrame(predictions), runs
```

- [ ] **Step 4: Implement ablations, intervals, confidence components, coefficients, and permutation importance**

Run: `pytest tests/unit/test_baselines.py tests/unit/test_model_validation.py tests/unit/test_model_metrics.py tests/integration/test_training_pipeline.py -q`

- [ ] **Step 5: Commit forecasting pipeline**

```bash
git add src/models tests
git commit -m "feat: add expanding-window forecasts"
```

### Task 7: Consensus modes and variant-perception signals

**Files:**
- Create: `src/consensus/__init__.py`, `src/consensus/base.py`, `src/consensus/csv_provider.py`, `src/consensus/proxy.py`, `src/consensus/variant.py`
- Create: `data/demo/consensus_template.csv`
- Test: `tests/unit/test_consensus.py`, `tests/unit/test_variant.py`, `tests/integration/test_variant_pipeline.py`

**Interfaces:**
- Produces: `ConsensusProvider.estimates(as_of: date) -> pd.DataFrame`
- Produces: `select_expectation(estimates, cutoff) -> Expectation | None`
- Produces: `build_variant_signals(forecasts, expectations) -> pd.DataFrame`

- [ ] **Step 1: Write failing as-of selection, proxy-label, z-score, and bucket tests**

```python
def test_consensus_selection_never_uses_future_revision(estimates):
    selected = select_expectation(estimates, cutoff=date(2024, 4, 20))
    assert selected.as_of_date == date(2024, 4, 18)


def test_proxy_is_never_labeled_actual(proxy_expectation):
    assert proxy_expectation.mode == "expectation_proxy"
    assert "consensus" not in proxy_expectation.display_label.lower()
```

- [ ] **Step 2: Verify failures**

Run: `pytest tests/unit/test_consensus.py tests/unit/test_variant.py -q`

- [ ] **Step 3: Implement validated CSV import, historical-only expectation proxy, cross-sectional z-scores, buckets, and confidence fields**

```python
def build_variant_signals(forecasts: pd.DataFrame, expectations: pd.DataFrame) -> pd.DataFrame:
    joined = forecasts.merge(
        expectations, on=["company_id", "fiscal_quarter", "forecast_cutoff_date"], validate="many_to_one"
    )
    joined["variant"] = (joined["forecast_revenue"] - joined["expectation_revenue"]) / joined["expectation_revenue"]
    joined["variant_zscore"] = joined.groupby(["forecast_cutoff_date", "horizon_days"])["variant"].transform(
        safe_zscore
    )
    joined["variant_bucket"] = joined.groupby(["forecast_cutoff_date", "horizon_days"])["variant"].transform(
        bucket_variants
    )
    return joined
```

- [ ] **Step 4: Run integration tests**

Run: `pytest tests/unit/test_consensus.py tests/unit/test_variant.py tests/integration/test_variant_pipeline.py -q`

- [ ] **Step 5: Commit expectation layer**

```bash
git add src/consensus data/demo/consensus_template.csv tests
git commit -m "feat: construct variant perception signals"
```

### Task 8: Event study, inference, robustness, and market-neutral portfolio

**Files:**
- Create: `src/backtest/event_study.py`, `src/backtest/statistics.py`, `src/backtest/portfolio.py`
- Test: `tests/unit/test_event_study.py`, `tests/unit/test_backtest_statistics.py`, `tests/unit/test_portfolio.py`, `tests/integration/test_backtest_pipeline.py`

**Interfaces:**
- Produces: `run_event_study(signals, prices, windows, benchmarks) -> EventStudyResult`
- Produces: `summarize_buckets(event_returns, bootstrap_samples, seed) -> pd.DataFrame`
- Produces: `run_event_portfolio(signals, prices, config) -> PortfolioResult`

- [ ] **Step 1: Write failing bucket, bootstrap, cost, overlap, and drawdown tests**

```python
def test_round_trip_costs_reduce_long_short_return(portfolio_fixture):
    gross = run_event_portfolio(**portfolio_fixture, transaction_cost_bps=0)
    net = run_event_portfolio(**portfolio_fixture, transaction_cost_bps=10)
    assert net.cumulative_return < gross.cumulative_return


def test_bucket_summary_reports_sample_and_interval(event_returns):
    summary = summarize_buckets(event_returns, bootstrap_samples=500, seed=42)
    assert summary.loc["strongly_positive", "n"] == 8
    assert summary.loc["strongly_positive", "ci_low"] <= summary.loc["strongly_positive", "mean"]
```

- [ ] **Step 2: Verify failures**

Run: `pytest tests/unit/test_event_study.py tests/unit/test_backtest_statistics.py tests/unit/test_portfolio.py -q`

- [ ] **Step 3: Implement event joins, raw/benchmark/sector adjustments, robust summaries, Newey-West regression, and multiple-testing caveats**

```python
def run_event_study(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    windows: Sequence[tuple[int, int]],
    benchmarks: Mapping[str, str],
) -> EventStudyResult:
    rows = [calculate_signal_windows(signal, prices, windows, benchmarks) for signal in signals.itertuples()]
    event_returns = pd.concat(rows, ignore_index=True)
    return EventStudyResult(event_returns=event_returns, bucket_summary=summarize_buckets(event_returns, 2_000, 42))
```

- [ ] **Step 4: Implement constrained equal-weight long/short event portfolio and metrics**

Run: `pytest tests/unit/test_event_study.py tests/unit/test_backtest_statistics.py tests/unit/test_portfolio.py tests/integration/test_backtest_pipeline.py -q`

- [ ] **Step 5: Commit backtesting**

```bash
git add src/backtest tests
git commit -m "feat: evaluate earnings-event signals"
```

### Task 9: Pipeline orchestration, truthful demo mode, and complete CLI

**Files:**
- Create: `src/pipeline.py`, `src/demo.py`
- Modify: `src/cli.py`, `Makefile`
- Test: `tests/unit/test_pipeline.py`, `tests/integration/test_cli.py`, `tests/integration/test_demo.py`

**Interfaces:**
- Produces: `Pipeline.run(stages: Sequence[str], mode: str) -> PipelineSummary`
- Produces: CLI commands required by the specification and `make demo`

- [ ] **Step 1: Write failing stage-order, restart, failure-propagation, and demo-truthfulness tests**

```python
def test_demo_builds_all_required_tables_and_labels_sources(demo_project):
    result = runner.invoke(app, ["demo", "--project-root", str(demo_project)])
    assert result.exit_code == 0
    assert required_tables <= set(database_tables(demo_project / "data/nowcaster.duckdb"))
    assert set(read_sources()) <= {
        "sec_public_snapshot",
        "wikimedia_public_snapshot",
        "fred_public_snapshot",
        "market_public_snapshot",
        "expectation_proxy",
    }
```

- [ ] **Step 2: Verify failures**

Run: `pytest tests/unit/test_pipeline.py tests/integration/test_cli.py tests/integration/test_demo.py -q`

- [ ] **Step 3: Implement all CLI commands, stage manifests, restartability, pipeline run records, and demo orchestration**

```python
@app.command("run-all")
def run_all(project_root: Path = Path.cwd(), mode: str = "live") -> None:
    settings = Settings.load(project_root, mode=mode)
    summary = Pipeline(settings).run(ALL_STAGES, mode=mode)
    typer.echo(summary.concise_message)
    if summary.failed:
        raise typer.Exit(code=1)
```

- [ ] **Step 4: Run CLI integration and `make demo`**

Run: `pytest tests/unit/test_pipeline.py tests/integration/test_cli.py tests/integration/test_demo.py -q && make demo`

- [ ] **Step 5: Commit orchestration**

```bash
git add src/cli.py src/pipeline.py src/demo.py Makefile tests
git commit -m "feat: orchestrate complete research pipeline"
```

### Task 10: Research report, case study, recruiter statistics, and resume bullets

**Files:**
- Create: `src/reporting/__init__.py`, `src/reporting/research_report.py`, `src/reporting/case_study.py`, `src/reporting/recruiter.py`
- Create: `reports/.gitkeep`
- Test: `tests/unit/test_reporting.py`, `tests/integration/test_report_pipeline.py`

**Interfaces:**
- Produces: `generate_research_report(db, output_path) -> Path`
- Produces: `select_case_study(db) -> CaseStudy | None`
- Produces: `generate_resume_bullets(db, output_path) -> Path`

- [ ] **Step 1: Write failing section, evidence, case-selection, and no-fabrication tests**

```python
def test_report_contains_all_required_sections_and_measured_counts(demo_db):
    text = generate_research_report(demo_db, report_path).read_text()
    assert all(section in text for section in REQUIRED_REPORT_SECTIONS)
    assert f"{actual_company_count(demo_db)} companies" in text


def test_resume_bullets_refuse_missing_metrics(empty_db):
    text = generate_resume_bullets(empty_db, output).read_text()
    assert "not generated" in text.lower()
    assert "X%" not in text
```

- [ ] **Step 2: Verify failures**

Run: `pytest tests/unit/test_reporting.py -q`

- [ ] **Step 3: Implement professional Markdown templates driven only by persisted evidence**

```python
def recruiter_statistics(db: Database) -> dict[str, int | float | None]:
    return {
        "companies": db.scalar("select count(distinct company_id) from financials_quarterly"),
        "company_quarters": db.scalar("select count(*) from financials_quarterly where quality_status = 'valid'"),
        "alternative_observations": db.scalar("select count(*) from alternative_data_daily"),
        "historical_forecasts": db.scalar("select count(*) from forecasts where status = 'success'"),
        "forecast_mae_improvement": measured_mae_improvement(db),
        "event_spread": measured_event_spread(db),
    }
```

- [ ] **Step 4: Generate demo reports and run integration assertions**

Run: `pytest tests/unit/test_reporting.py tests/integration/test_report_pipeline.py -q && python -m src.cli report --mode demo`

- [ ] **Step 5: Commit reporting**

```bash
git add src/reporting reports tests
git commit -m "feat: generate evidence-backed research artifacts"
```

### Task 11: Six-page Streamlit research dashboard

**Files:**
- Create: `dashboard/app.py`, `dashboard/data.py`, `dashboard/theme.py`, `dashboard/components.py`
- Create: `dashboard/pages/1_Overview.py`, `2_Company_Research.py`, `3_Forecast_Monitor.py`, `4_Model_Performance.py`, `5_Event_Study.py`, `6_Data_Quality.py`
- Test: `tests/unit/test_dashboard_data.py`, `tests/integration/test_dashboard_smoke.py`

**Interfaces:**
- Produces: cached read-only functions in `dashboard.data` returning typed view frames
- Produces: Streamlit application at `dashboard/app.py`

- [ ] **Step 1: Write failing dashboard query-contract and empty-state tests**

```python
def test_overview_view_exposes_mode_freshness_and_sample_counts(demo_db):
    view = load_overview(demo_db.url)
    assert view.data_mode == "demo_real_snapshot"
    assert view.company_count > 0
    assert view.historical_forecast_count > 0
```

- [ ] **Step 2: Verify failures**

Run: `pytest tests/unit/test_dashboard_data.py -q`

- [ ] **Step 3: Implement shared institutional visual system, mode badges, filters, empty/error states, and six pages**

```python
@st.cache_data(show_spinner=False)
def load_forecast_monitor(database_url: str, horizon: int) -> pd.DataFrame:
    db = Database.from_url(database_url)
    return db.frame(FORECAST_MONITOR_QUERY, {"horizon": horizon}).sort_values("absolute_variant", ascending=False)
```

- [ ] **Step 4: Start Streamlit headlessly and run dashboard smoke tests**

Run: `pytest tests/unit/test_dashboard_data.py tests/integration/test_dashboard_smoke.py -q && streamlit run dashboard/app.py --server.headless true --server.port 8511`

- [ ] **Step 5: Commit dashboard**

```bash
git add dashboard tests
git commit -m "feat: add Streamlit research dashboard"
```

### Task 12: Documentation, notebooks, macOS setup, and recruiter polish

**Files:**
- Create: `README.md`, `docs/architecture.md`, `docs/methodology.md`, `docs/data_dictionary.md`, `docs/interview_guide.md`
- Create: `notebooks/01_data_exploration.ipynb`, `notebooks/02_feature_research.ipynb`, `notebooks/03_model_analysis.ipynb`
- Create: `scripts/capture_dashboard.py`
- Create: `docs/images/*.png`
- Test: `tests/unit/test_documentation.py`, `tests/integration/test_notebooks.py`

**Interfaces:**
- Consumes: verified demo database, reports, and dashboard
- Produces: recruiter-quality entry documentation and verified screenshots

- [ ] **Step 1: Write failing documentation-link, command, notebook-import, and disclaimer tests**

```python
def test_readme_documents_definition_of_done_commands():
    text = Path("README.md").read_text()
    for command in ("make demo", "make test", "make dashboard"):
        assert command in text
    assert "not investment advice" in text.lower()
```

- [ ] **Step 2: Verify failures**

Run: `pytest tests/unit/test_documentation.py tests/integration/test_notebooks.py -q`

- [ ] **Step 3: Write README, architecture, methodology, dictionary, interview guide, and import-only notebooks**

- [ ] **Step 4: Capture and inspect all six dashboard pages, then update README screenshots**

Run: `python scripts/capture_dashboard.py --database-url duckdb:///data/nowcaster.duckdb --output docs/images`

- [ ] **Step 5: Commit documentation**

```bash
git add README.md docs notebooks scripts tests
git commit -m "docs: finish recruiter-facing project guide"
```

### Task 13: Full verification and requirement audit

**Files:**
- Modify: any failing implementation or documentation file in scope
- Create: `docs/verification.md`

**Interfaces:**
- Produces: authoritative evidence for every definition-of-done requirement

- [ ] **Step 1: Run formatting, linting, typing, and the full test suite**

Run: `ruff format --check . && ruff check . && pytest --cov=src --cov=dashboard --cov-report=term-missing -q`

- [ ] **Step 2: Rebuild from a clean generated-data state using public demo snapshots**

Run: `make clean-generated && make demo`

- [ ] **Step 3: Audit database tables, row counts, source labels, leakage invariants, model folds, reports, and resume bullets**

```python
assert not db.frame("select * from features_quarterly where maximum_input_available_date > forecast_cutoff_date").shape[
    0
]
assert set(required_tables).issubset(database_tables)
assert all(source_mode != "synthetic" for source_mode in demo_source_modes)
```

- [ ] **Step 4: Verify all CLI commands and Streamlit health on macOS**

Run: `python -m src.cli --help && make dashboard-smoke`

- [ ] **Step 5: Write `docs/verification.md`, review Git diff/status, and commit only verified fixes**

```bash
git add docs/verification.md
git commit -m "test: verify complete nowcaster project"
```
