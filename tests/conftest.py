from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "model.yaml").write_text(
        "forecast_horizons: [30, 14, 7, 1]\nminimum_training_quarters: 8\nrandom_seed: 42\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "universe.yaml").write_text(
        "companies:\n  - ticker: SBUX\n    cik: '829224'\n    name: Starbucks Corporation\n    enabled: true\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "features.yaml").write_text(
        "availability_lags:\n  wikipedia: 1\n",
        encoding="utf-8",
    )
    return tmp_path


@pytest.fixture
def model_matrix():
    rows = []
    dates = pd.date_range("2018-02-01", periods=20, freq="QE")
    for index, cutoff in enumerate(dates):
        for company_index, company in enumerate(("SBUX", "MCD")):
            baseline = 100 + company_index * 50 + index * 2
            attention = 10 + index + company_index
            actual = baseline * (1 + attention / 1000)
            rows.append(
                {
                    "company_id": company,
                    "fiscal_quarter": f"{2018 + index // 4}Q{index % 4 + 1}",
                    "forecast_cutoff_date": cutoff.date(),
                    "horizon_days": 7,
                    "actual_revenue": actual,
                    "revenue_year_ago": baseline - 8,
                    "revenue_level_lag1": baseline - 2,
                    "revenue_qoq_log_growth_lag1": np.log(baseline / (baseline - 2)),
                    "revenue_yoy_log_growth_lag1": np.log(baseline / (baseline - 8)),
                    "seasonal_quarter": index % 4 + 1,
                    "wikipedia_pageviews_trailing_mean": attention,
                    "wikipedia_pageviews_momentum": index / 100,
                    "macro_RSAFS_level": 500 + index,
                    "actual_acceleration": 1 if index % 2 else -1,
                }
            )
    return pd.DataFrame(rows)


@pytest.fixture(scope="session")
def demo_database(tmp_path_factory):
    from src.config.settings import Settings
    from src.database.engine import Database
    from src.demo import run_demo

    root = Path(__file__).resolve().parents[1]
    database_path = tmp_path_factory.mktemp("shared-demo") / "demo.duckdb"
    settings = Settings.load(root, mode="demo").model_copy(update={"database_url": f"duckdb:///{database_path}"})
    summary = run_demo(settings)
    if summary.failed:
        raise RuntimeError(summary.concise_message)
    return settings, Database.from_url(settings.database_url)
