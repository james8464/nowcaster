from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta
from typing import Any

import numpy as np
import pandas as pd

from src.features.aggregation import aggregate_attention_as_of
from src.features.leakage import assert_no_lookahead
from src.ingestion.macro import validate_point_in_time_macro
from src.utils.provenance import canonical_hash

TRANSFORMATION_VERSION = "pit-features-v1"


class FeatureBuilder:
    def __init__(
        self,
        financials: pd.DataFrame,
        earnings: pd.DataFrame,
        alternative: pd.DataFrame,
        macro: pd.DataFrame | None = None,
    ):
        self.financials = financials.copy()
        self.earnings = earnings.copy()
        self.alternative = alternative.copy()
        self.macro = macro.copy() if macro is not None else pd.DataFrame()

    @staticmethod
    def _feature(
        *,
        company_id: str,
        fiscal_quarter: str,
        earnings_date: date,
        cutoff: date,
        horizon: int,
        name: str,
        value: float | int | None,
        family: str,
        maximum_available: date,
    ) -> dict[str, Any]:
        return {
            "company_id": company_id,
            "fiscal_quarter": fiscal_quarter,
            "earnings_date": earnings_date,
            "forecast_cutoff_date": cutoff,
            "horizon_days": horizon,
            "feature_name": name,
            "feature_value": float(value) if value is not None and not pd.isna(value) else math.nan,
            "feature_family": family,
            "maximum_input_available_date": maximum_available,
            "transformation_version": TRANSFORMATION_VERSION,
        }

    def build(self, *, horizons: list[int] | tuple[int, ...]) -> pd.DataFrame:
        if not horizons or any(horizon <= 0 for horizon in horizons):
            raise ValueError("Forecast horizons must be positive")
        financials = self.financials.copy()
        for column in ("period_start", "period_end", "available_date"):
            financials[column] = pd.to_datetime(financials[column]).dt.date
        financials = financials.sort_values(["company_id", "period_end", "available_date"])
        earnings = self.earnings.copy()
        earnings["earnings_date"] = pd.to_datetime(earnings["earnings_date"]).dt.date
        alternative = self.alternative.copy()
        if not alternative.empty:
            alternative["observation_date"] = pd.to_datetime(alternative["observation_date"]).dt.date
            alternative["available_date"] = pd.to_datetime(alternative["available_date"]).dt.date
        if not self.macro.empty:
            validate_point_in_time_macro(self.macro)

        rows: list[dict[str, Any]] = []
        for event in earnings.itertuples(index=False):
            target = financials[
                (financials["company_id"] == event.company_id) & (financials["fiscal_quarter"] == event.fiscal_quarter)
            ]
            if target.empty:
                continue
            quarter_number = int(str(event.fiscal_quarter).split("Q")[-1])
            for horizon in horizons:
                cutoff = event.earnings_date - timedelta(days=horizon)
                known = financials[
                    (financials["company_id"] == event.company_id) & (financials["available_date"] <= cutoff)
                ].drop_duplicates("fiscal_quarter", keep="last")
                known = known.sort_values("period_end")
                if known.empty:
                    continue
                latest = known.iloc[-1]
                common = {
                    "company_id": event.company_id,
                    "fiscal_quarter": event.fiscal_quarter,
                    "earnings_date": event.earnings_date,
                    "cutoff": cutoff,
                    "horizon": horizon,
                }
                rows.append(
                    self._feature(
                        **common,
                        name="revenue_level_lag1",
                        value=latest.revenue,
                        family="fundamentals",
                        maximum_available=latest.available_date,
                    )
                )
                rows.append(
                    self._feature(
                        **common,
                        name="seasonal_quarter",
                        value=quarter_number,
                        family="calendar",
                        maximum_available=cutoff,
                    )
                )
                if len(known) >= 2 and known.iloc[-2].revenue > 0:
                    rows.append(
                        self._feature(
                            **common,
                            name="revenue_qoq_log_growth_lag1",
                            value=np.log(latest.revenue / known.iloc[-2].revenue),
                            family="fundamentals",
                            maximum_available=max(latest.available_date, known.iloc[-2].available_date),
                        )
                    )
                if len(known) >= 5 and known.iloc[-5].revenue > 0:
                    rows.append(
                        self._feature(
                            **common,
                            name="revenue_yoy_log_growth_lag1",
                            value=np.log(latest.revenue / known.iloc[-5].revenue),
                            family="fundamentals",
                            maximum_available=max(latest.available_date, known.iloc[-5].available_date),
                        )
                    )
                target_year = int(str(event.fiscal_quarter).split("Q")[0]) - 1
                year_ago_quarter = f"{target_year}Q{quarter_number}"
                year_ago = known[known["fiscal_quarter"] == year_ago_quarter]
                if not year_ago.empty:
                    row = year_ago.iloc[-1]
                    rows.append(
                        self._feature(
                            **common,
                            name="revenue_year_ago",
                            value=row.revenue,
                            family="fundamentals",
                            maximum_available=row.available_date,
                        )
                    )

                company_alternative = (
                    alternative[alternative["company_id"] == event.company_id] if not alternative.empty else alternative
                )
                for signal, signal_frame in company_alternative.groupby("signal"):
                    aggregates = aggregate_attention_as_of(signal_frame, cutoff=cutoff, trailing_days=28)
                    maximum_available = aggregates.pop("maximum_input_available_date")
                    for metric_name, value in aggregates.items():
                        rows.append(
                            self._feature(
                                **common,
                                name=f"{signal}_{metric_name}",
                                value=value,
                                family="alternative",
                                maximum_available=maximum_available,
                            )
                        )

                if not self.macro.empty:
                    macro = self.macro.copy()
                    macro["available_date"] = pd.to_datetime(macro["available_date"]).dt.date
                    eligible_macro = macro[macro["available_date"] <= cutoff]
                    for series_id, series in eligible_macro.groupby("series_id"):
                        value_row = series.sort_values(["observation_date", "vintage_date"]).iloc[-1]
                        rows.append(
                            self._feature(
                                **common,
                                name=f"macro_{series_id}_level",
                                value=value_row.value,
                                family="macro",
                                maximum_available=value_row.available_date,
                            )
                        )
        columns = [
            "company_id",
            "fiscal_quarter",
            "earnings_date",
            "forecast_cutoff_date",
            "horizon_days",
            "feature_name",
            "feature_value",
            "feature_family",
            "maximum_input_available_date",
            "transformation_version",
        ]
        result = pd.DataFrame(rows, columns=columns)
        if not result.empty:
            result = result.sort_values(
                ["forecast_cutoff_date", "company_id", "fiscal_quarter", "horizon_days", "feature_name"]
            ).reset_index(drop=True)
            assert_no_lookahead(result)
        return result


def feature_rows(frame: pd.DataFrame, *, source: str = "point_in_time_feature_engine") -> list[dict[str, Any]]:
    created_at = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for row in frame.itertuples(index=False):
        values = row._asdict()
        values.update(
            {
                "feature_id": canonical_hash(
                    [
                        row.company_id,
                        row.fiscal_quarter,
                        row.horizon_days,
                        row.feature_name,
                        row.transformation_version,
                    ]
                )[:24],
                "source": source,
                "source_version": TRANSFORMATION_VERSION,
                "created_at": created_at,
            }
        )
        rows.append(values)
    return rows
