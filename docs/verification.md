# Verification record

Verified on 2026-08-22 on macOS with Python 3.13.14. This record describes the bundled `demo_real_snapshot` mode; it is not evidence that a deployable trading strategy exists.

## Build and automated checks

- Clean rebuild: `make clean-generated && make demo` completed all seven stages with zero reused stages in 48 seconds.
- Formatting and linting: `ruff format --check .` and `ruff check .` passed repository-wide.
- Import/bytecode check: `python -m compileall -q src dashboard scripts` passed.
- Tests: 86 passed in 87.53 seconds with 83% combined `src`/`dashboard` line coverage.
- CLI: the root help and every subcommand help path passed, including `run-all`.
- Dashboard: the headless Streamlit health check returned `ok`; all six pages were recaptured and visually inspected at 1600×1000.
- Notebooks: all three notebooks executed through the notebook integration test.

## Persisted demo evidence

| Dataset | Rows |
|---|---:|
| Companies | 3 |
| SEC quarterly financials | 155 |
| Earnings-date proxies | 155 |
| Adjusted daily prices | 26,616 |
| Daily Wikimedia observations | 12,210 |
| Point-in-time feature rows | 6,604 |
| Out-of-sample forecasts | 2,047 |
| Expectation proxies | 128 |
| Variant signals | 2,047 |
| Signal/event-window observations | 8,188 |

The 16 raw files referenced by the four snapshot manifests matched their recorded SHA-256 hashes. Natural-key duplicate checks returned zero for companies, financials, earnings dates, prices, alternative data, features, forecasts, variants, and event-study results. Source-label review found no synthetic or fake source claims. Latest-revised macro files were correctly excluded, leaving zero historical macro rows.

## Leakage and join invariants

- Feature rows with `maximum_input_available_date > forecast_cutoff_date`: 0.
- Model runs with `training_end >= test_start`: 0.
- Model-to-expectation revisions dated after cutoff: 0.
- Forecasts without a matched variant: 0.
- Signals without exactly four configured event windows: 0.
- Alternative observations available before their observation date: 0.

Models are fit independently for the 1-, 7-, 14-, and 30-day horizons. A target can enter training only after its earnings/filing availability date, preventing an earlier-cutoff row for an unresolved quarter from leaking the target into a shorter-horizon fold. A dedicated regression test covers this cross-horizon failure mode.

## Measured research results

| Horizon | Matched observations | Full Ridge vs seasonal MAE | Attention increment vs fundamentals-only Ridge |
|---:|---:|---:|---:|
| 1 day | 128 | 29.7% better | 6.8% worse |
| 7 days | 128 | 27.7% better | 9.8% worse |
| 14 days | 127 | 29.2% better | 6.1% worse |
| 30 days | 128 | 26.5% better | 10.7% worse |
| Combined matched sample | 511 | 28.3% better | 8.4% worse |

One 14-day fundamentals-plus-attention linear forecast was nonpositive and was excluded by the explicit positive-revenue persistence rule, producing 127 rather than 128 matched observations at that horizon. The [0,+3] top-minus-bottom market-adjusted return spread was -0.04%.

## Release assessment

**Software/demo: verified. Research conclusion: share with caveats.** The project is suitable as a reproducible engineering and investment-research portfolio artifact. The current public-data sample does not support a profitable long/short claim: attention worsened the matched fundamentals-only forecast, the event spread was economically negligible and negative, event dates and expectations are transparent proxies, and the universe contains only three companies. Any live-trading use would require licensed point-in-time consensus, precise event timestamps, a much larger preregistered out-of-sample study, execution/borrow/cost controls, and paper-trading validation.
