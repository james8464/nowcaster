from __future__ import annotations

import pandas as pd

from dashboard.data import forecast_metrics, load_forecast_monitor, load_overview
from src.database.engine import Database


def test_overview_exposes_mode_freshness_and_zero_safe_sample_counts(tmp_path):
    database_url = f"duckdb:///{tmp_path / 'dashboard.duckdb'}"
    database = Database.from_url(database_url)
    database.initialize()

    view = load_overview(database_url)

    assert view.data_mode == "uninitialized"
    assert view.company_count == 0
    assert view.historical_forecast_count == 0
    assert view.latest_refresh is None


def test_forecast_monitor_empty_state_has_stable_columns(tmp_path):
    database_url = f"duckdb:///{tmp_path / 'monitor.duckdb'}"
    database = Database.from_url(database_url)
    database.initialize()

    frame = load_forecast_monitor(database_url, horizon=7)

    assert frame.empty
    assert {"company_id", "forecast_revenue", "expectation_revenue", "variant", "absolute_variant"} <= set(frame)


def test_model_metrics_reconcile_mae_and_directional_accuracy():
    frame = pd.DataFrame(
        {
            "model_name": ["ridge", "ridge"],
            "ablation": ["fundamentals_alt", "fundamentals_alt"],
            "horizon_days": [7, 7],
            "forecast_revenue": [110.0, 90.0],
            "actual_revenue": [100.0, 100.0],
            "forecast_acceleration": [1, -1],
            "actual_acceleration": [1, 1],
        }
    )

    metrics = forecast_metrics(frame).iloc[0]

    assert metrics.mae == 10.0
    assert metrics.directional_accuracy == 0.5
    assert metrics.n == 2
