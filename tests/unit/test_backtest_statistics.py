from __future__ import annotations

import pandas as pd

from src.backtest.statistics import newey_west_variant_regression, summarize_buckets


def test_bucket_summary_reports_sample_interval_and_hit_rate():
    event_returns = pd.DataFrame(
        {
            "variant_bucket": ["strongly_positive"] * 8 + ["strongly_negative"] * 4,
            "abnormal_return": [0.01, 0.02, 0.03, -0.01, 0.04, 0.01, 0.02, 0.03, -0.01, -0.02, 0.0, 0.01],
        }
    )

    summary = summarize_buckets(event_returns, bootstrap_samples=500, seed=42)

    assert summary.loc["strongly_positive", "n"] == 8
    assert summary.loc["strongly_positive", "ci_low"] <= summary.loc["strongly_positive", "mean"]
    assert summary.loc["strongly_positive", "ci_high"] >= summary.loc["strongly_positive", "mean"]
    assert summary.loc["strongly_positive", "hit_rate"] == 7 / 8


def test_newey_west_regression_reports_observations_and_caveat():
    frame = pd.DataFrame(
        {"variant_zscore": [-2, -1, 0, 1, 2, 3], "abnormal_return": [-0.03, -0.02, 0, 0.01, 0.03, 0.04]}
    )

    result = newey_west_variant_regression(frame, max_lags=1)

    assert result["n"] == 6
    assert result["coefficient"] > 0
    assert "multiple" in result["caveat"].lower()
