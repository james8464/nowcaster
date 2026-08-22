from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from src.utils.provenance import canonical_hash

REQUIRED_COLUMNS = {
    "ticker",
    "fiscal_quarter",
    "as_of_date",
    "consensus_revenue",
    "consensus_eps",
    "number_of_analysts",
}


class CsvConsensusProvider:
    """Validated user-supplied historical consensus snapshots."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def estimates(self, as_of: date) -> pd.DataFrame:
        frame = pd.read_csv(self.path)
        missing = REQUIRED_COLUMNS - set(frame.columns)
        if missing:
            raise ValueError(f"Consensus CSV is missing columns: {sorted(missing)}")
        frame = frame.copy()
        frame["ticker"] = frame["ticker"].astype(str).str.strip().str.upper()
        frame["fiscal_quarter"] = frame["fiscal_quarter"].astype(str).str.strip().str.upper()
        invalid_quarters = ~frame["fiscal_quarter"].str.match(r"^\d{4}Q[1-4]$")
        if invalid_quarters.any():
            raise ValueError("Consensus CSV contains invalid fiscal_quarter values")
        frame["as_of_date"] = pd.to_datetime(frame["as_of_date"], errors="raise").dt.date
        frame["consensus_revenue"] = pd.to_numeric(frame["consensus_revenue"], errors="raise")
        frame["consensus_eps"] = pd.to_numeric(frame["consensus_eps"], errors="coerce")
        frame["number_of_analysts"] = pd.to_numeric(frame["number_of_analysts"], errors="coerce").astype("Int64")
        if (frame["consensus_revenue"] <= 0).any():
            raise ValueError("Consensus revenue must be positive")
        frame = frame[frame["as_of_date"] <= as_of].rename(columns={"ticker": "company_id"})
        frame["mode"] = "manual_csv"
        frame["source"] = "user_supplied_consensus_csv"
        frame["source_version"] = canonical_hash([self.path.read_bytes()])[:16]
        frame["created_at"] = datetime.now(UTC)
        frame["estimate_id"] = frame.apply(
            lambda row: canonical_hash(
                [row.company_id, row.fiscal_quarter, row.as_of_date, row.consensus_revenue, "manual_csv"]
            )[:24],
            axis=1,
        )
        if frame.duplicated(["company_id", "fiscal_quarter", "as_of_date", "mode"]).any():
            raise ValueError("Consensus CSV contains duplicate snapshots")
        return frame.sort_values(["company_id", "fiscal_quarter", "as_of_date"]).reset_index(drop=True)
