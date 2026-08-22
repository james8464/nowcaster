from __future__ import annotations

import pandas as pd


class LookaheadError(ValueError):
    pass


def assert_no_lookahead(features: pd.DataFrame) -> None:
    required = {"company_id", "fiscal_quarter", "feature_name", "forecast_cutoff_date", "maximum_input_available_date"}
    if missing := required - set(features.columns):
        raise ValueError(f"Feature matrix missing leakage-audit columns: {', '.join(sorted(missing))}")
    cutoff = pd.to_datetime(features["forecast_cutoff_date"])
    available = pd.to_datetime(features["maximum_input_available_date"])
    leaked = features[available > cutoff]
    if not leaked.empty:
        records = ", ".join(
            f"{row.company_id}:{row.fiscal_quarter}:{row.feature_name}" for row in leaked.itertuples(index=False)
        )
        raise LookaheadError(f"Point-in-time leakage detected in {records}")
