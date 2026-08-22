from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.ingestion.http import CachedHttpClient, RateLimiter
from src.utils.provenance import canonical_hash


@dataclass(frozen=True)
class MacroObservation:
    series_id: str
    observation_date: date
    available_date: date
    vintage_date: date
    value: float
    unit: str

    def __post_init__(self) -> None:
        if self.available_date < self.observation_date:
            raise ValueError("Macro availability cannot precede observation")
        if self.vintage_date < self.available_date:
            raise ValueError("Macro vintage cannot precede availability")


def parse_fred(payload: dict[str, Any], series_id: str) -> list[MacroObservation]:
    unit = str(payload.get("units", "unknown"))
    rows: list[MacroObservation] = []
    for item in payload.get("observations", []):
        if item.get("value") in {None, ".", ""}:
            continue
        available_date = date.fromisoformat(item["realtime_start"])
        rows.append(
            MacroObservation(
                series_id=series_id,
                observation_date=date.fromisoformat(item["date"]),
                available_date=available_date,
                vintage_date=available_date,
                value=float(item["value"]),
                unit=unit,
            )
        )
    return sorted(rows, key=lambda row: (row.observation_date, row.vintage_date))


class FredProvider:
    def __init__(self, http: CachedHttpClient, api_key: str, *, requests_per_second: float = 2):
        if not api_key:
            raise ValueError("FRED_API_KEY is required for point-in-time macro data")
        self.http = http
        self.api_key = api_key
        self.limiter = RateLimiter(requests_per_second)

    def fetch(self, series_id: str, start: date, end: date, *, vintage_date: date) -> list[MacroObservation]:
        self.limiter.wait()
        payload = self.http.get_json(
            "https://api.stlouisfed.org/fred/series/observations",
            cache_key=f"fred_{series_id}_{start}_{end}_{vintage_date}",
            params={
                "api_key": self.api_key,
                "file_type": "json",
                "series_id": series_id,
                "observation_start": start.isoformat(),
                "observation_end": end.isoformat(),
                "realtime_start": vintage_date.isoformat(),
                "realtime_end": vintage_date.isoformat(),
            },
        )
        return parse_fred(payload, series_id)


class CsvMacroProvider:
    def __init__(self, paths: dict[str, Path]):
        self.paths = {key: Path(value) for key, value in paths.items()}

    def fetch(self, series_id: str, start: date, end: date) -> list[MacroObservation]:
        frame = pd.read_csv(self.paths[series_id])
        required = {"observation_date", "available_date", "vintage_date", "value", "unit"}
        if missing := required - set(frame.columns):
            raise ValueError(f"Macro CSV missing columns: {', '.join(sorted(missing))}")
        rows = [
            MacroObservation(
                series_id=series_id,
                observation_date=pd.Timestamp(row.observation_date).date(),
                available_date=pd.Timestamp(row.available_date).date(),
                vintage_date=pd.Timestamp(row.vintage_date).date(),
                value=float(row.value),
                unit=str(row.unit),
            )
            for row in frame.itertuples(index=False)
        ]
        return [row for row in rows if start <= row.observation_date <= end]


def validate_point_in_time_macro(frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    if "source_version" in frame and frame["source_version"].astype(str).str.contains("latest_revised").any():
        raise ValueError("Macro data is not vintage-safe: latest revised values were supplied")
    if (pd.to_datetime(frame["available_date"]) < pd.to_datetime(frame["observation_date"])).any():
        raise ValueError("Macro data contains availability before observation")


def macro_rows(
    observations: list[MacroObservation],
    *,
    source: str = "fred_alfred_api",
    source_version: str = "alfred-vintage-v1",
) -> list[dict[str, object]]:
    created_at = datetime.now(UTC)
    rows: list[dict[str, object]] = []
    for observation in observations:
        values = asdict(observation)
        values.update(
            {
                "macro_id": canonical_hash(
                    [observation.series_id, observation.observation_date, observation.vintage_date, source]
                )[:24],
                "source": source,
                "source_version": source_version,
                "created_at": created_at,
            }
        )
        rows.append(values)
    return rows
