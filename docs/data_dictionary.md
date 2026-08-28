# Data dictionary

All dates use ISO calendar dates. `available_date` means the earliest date an input was permitted to enter a historical feature. Derived tables also carry `source`, `source_version`, and `created_at` unless noted.

| Table | Grain / primary identifier | Purpose and important fields |
|---|---|---|
| `companies` | one row per `company_id` | Ticker, CIK, name, sector, sector ETF, fiscal year end, active flag |
| `financials_quarterly` | company × fiscal quarter × accession | Period dates, filed/available dates, selected XBRL tag, revenue, operating/net income, EPS, quality status |
| `company_kpis` | company × quarter × KPI × accession | Optional operational KPI, unit, availability, evidence URL, extraction method |
| `earnings_calendar` | company × fiscal quarter | Earnings/event date, timing confidence, availability; demo uses filing-date proxy |
| `market_prices_daily` | symbol × trading date × source | Raw/adjusted close, volume, currency, adjustment status |
| `alternative_data_daily` | company × signal × observation date × source | Value, unit, availability date, source dimensions |
| `macro_data` | series × observation × vintage × source | Vintage-safe value, availability and vintage dates, unit |
| `features_quarterly` | company × quarter × horizon × feature × version | Cutoff, value, family, maximum input availability, transformation version |
| `model_runs` | `run_id` | Git revision, seed, model, feature set, train/test window, parameters, observations, metrics, status |
| `forecasts` | run × company × quarter × model × horizon × ablation | Revenue/target forecast, actual, interval, research confidence, explanation, OOS status |
| `consensus_estimates` | company × quarter × as-of × mode | Revenue/EPS expectation, analyst count, mode (`manual_csv`, `api`, or `expectation_proxy`) |
| `variant_signals` | forecast × estimate | Cutoff, normalized variant, z-score, bucket, confidence components, expectation mode |
| `backtest_results` | signal × event window | Raw, benchmark, sector, abnormal and sector-adjusted returns, costs, liquidity status |
| `data_quality_issues` | `issue_id` | Stage, entity, severity, rule, observed value, message, detection time |
| `pipeline_runs` | `pipeline_run_id` | Command/stage, mode, configuration hash, Git revision, timestamps, row counts, status, error |

## Native signal evidence fields

The app snapshot adds these nullable fields to a research signal. Nullable means “not measured,” never zero:

| Field | Plain-English meaning |
|---|---|
| `provider`, `feed`, `venue`, `product` | Exact market-data and tradable-product identity represented by the evidence. |
| `probability_definition` | The observed event being estimated, such as target-before-stop after costs. |
| `probability_lower_bound`, `probability_upper_bound` | Conservative uncertainty range around `calibrated_probability`. |
| `calibration_observations` | Raw number of out-of-fold outcomes used for calibration. |
| `calibration_effective_observations` | Dependence-adjusted information count; repeated/correlated outcomes count less. |
| `brier_score`, `expected_calibration_error` | Historical probability-quality diagnostics; lower is better, but neither proves future accuracy. |
| `gross_edge`, `estimated_cost`, `lower_net_edge` | Expected return before costs, modeled costs, and the conservative net bound after uncertainty. |
| `model_age_seconds`, `regime`, `latency_ms` | Freshness and current operating context. |
| `drift_status`, `drift_score` | Whether live evidence still resembles the sealed reference. |
| `coverage_ratio`, `coverage_status` | Fraction/status of observations on which the selective model was willing to issue a prediction. |

## Units

SEC monetary values retain issuer filing units (normally USD). Dashboard cards may display billions for readability. Returns, variants, growth, and MAPE are stored as decimal fractions. Confidence scores use a 0–100 research scale. Transaction-cost fields are decimal-return deductions.

## Natural-key policy

Natural keys prevent duplicate source observations. IDs are deterministic truncated SHA-256 hashes over stable business keys. Pipeline stages use append-only success/failure records; a completed stage is reused only for the same mode and configuration hash.
