from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from src.consensus.base import select_expectation
from src.consensus.csv_provider import CsvConsensusProvider
from src.consensus.proxy import historical_expectation_proxy


def test_consensus_selection_never_uses_future_revision():
    estimates = pd.DataFrame(
        [
            {
                "company_id": "SBUX",
                "fiscal_quarter": "2024Q2",
                "as_of_date": date(2024, 4, 18),
                "consensus_revenue": 9000.0,
                "mode": "manual_csv",
            },
            {
                "company_id": "SBUX",
                "fiscal_quarter": "2024Q2",
                "as_of_date": date(2024, 4, 22),
                "consensus_revenue": 9100.0,
                "mode": "manual_csv",
            },
        ]
    )

    selected = select_expectation(estimates, date(2024, 4, 20), company_id="SBUX", fiscal_quarter="2024Q2")

    assert selected is not None
    assert selected.as_of_date == date(2024, 4, 18)
    assert selected.revenue == 9000.0


def test_csv_provider_rejects_invalid_schema(tmp_path):
    path = tmp_path / "consensus.csv"
    pd.DataFrame([{"ticker": "SBUX", "fiscal_quarter": "2024Q2"}]).to_csv(path, index=False)

    with pytest.raises(ValueError, match="missing columns"):
        CsvConsensusProvider(path).estimates(date(2024, 4, 20))


def test_proxy_is_never_labeled_actual_consensus():
    financials = pd.DataFrame(
        [
            {"company_id": "SBUX", "fiscal_quarter": "2023Q2", "revenue": 8700.0, "available_date": date(2023, 5, 5)},
            {"company_id": "SBUX", "fiscal_quarter": "2024Q1", "revenue": 9400.0, "available_date": date(2024, 2, 1)},
        ]
    )

    proxy = historical_expectation_proxy(
        financials, company_id="SBUX", fiscal_quarter="2024Q2", cutoff=date(2024, 4, 20)
    )

    assert proxy is not None
    assert proxy.mode == "expectation_proxy"
    assert "consensus" not in proxy.display_label.lower()
    assert proxy.revenue == 8700.0
