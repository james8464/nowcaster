from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from io import StringIO
from pathlib import Path
from typing import Protocol

import httpx
import pandas as pd

from src.utils.provenance import canonical_hash

PRICE_COLUMNS = [
    "symbol",
    "trading_date",
    "raw_close",
    "adjusted_close",
    "volume",
    "currency",
    "adjustment_status",
]


class PriceProvider(Protocol):
    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        raise NotImplementedError


def normalize_prices(raw: pd.DataFrame, symbol: str, *, currency: str = "USD") -> pd.DataFrame:
    aliases = {str(column).strip().lower().replace("_", " "): column for column in raw.columns}
    date_column = aliases.get("date") or aliases.get("trading date")
    close_column = aliases.get("close")
    adjusted_column = aliases.get("adj close") or aliases.get("adjusted close")
    volume_column = aliases.get("volume")
    if date_column is None or close_column is None:
        raise ValueError("Price data requires Date and Close columns")

    trading_dates = pd.to_datetime(raw[date_column], errors="coerce").dt.date
    if trading_dates.isna().any():
        raise ValueError("Price data contains invalid dates")
    if trading_dates.duplicated().any():
        raise ValueError("Price data contains duplicate trading dates")
    raw_close = pd.to_numeric(raw[close_column], errors="coerce")
    adjusted_close = pd.to_numeric(raw[adjusted_column], errors="coerce") if adjusted_column else raw_close.copy()
    if raw_close.isna().any() or adjusted_close.isna().any() or (adjusted_close <= 0).any():
        raise ValueError("Price data contains missing or non-positive closes")

    result = pd.DataFrame(
        {
            "symbol": symbol.upper(),
            "trading_date": trading_dates,
            "raw_close": raw_close.astype(float),
            "adjusted_close": adjusted_close.astype(float),
            "volume": pd.to_numeric(raw[volume_column], errors="coerce") if volume_column else float("nan"),
            "currency": currency,
            "adjustment_status": "provider_adjusted" if adjusted_column else "raw_only",
        }
    )
    return result.sort_values("trading_date").reset_index(drop=True)[PRICE_COLUMNS]


class CsvPriceProvider:
    def __init__(self, paths: Mapping[str, Path], *, currency: str = "USD"):
        self.paths = {symbol.upper(): Path(path) for symbol, path in paths.items()}
        self.currency = currency

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        symbol = symbol.upper()
        if symbol not in self.paths:
            raise KeyError(f"No CSV configured for {symbol}")
        frame = normalize_prices(pd.read_csv(self.paths[symbol]), symbol, currency=self.currency)
        return frame[frame["trading_date"].between(start, end)].reset_index(drop=True)


class StooqPriceProvider:
    """Keyless daily-CSV adapter; Stooq offers no uptime or adjustment guarantee."""

    def __init__(self, cache_dir: Path, *, timeout_seconds: float = 30):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        stooq_symbol = symbol.lower() if symbol.startswith("^") else f"{symbol.lower()}.us"
        url = "https://stooq.com/q/d/l/"
        params = {
            "s": stooq_symbol,
            "d1": start.strftime("%Y%m%d"),
            "d2": end.strftime("%Y%m%d"),
            "i": "d",
        }
        cache_path = self.cache_dir / f"stooq_{symbol.upper()}_{params['d1']}_{params['d2']}.csv"
        if cache_path.exists():
            text = cache_path.read_text(encoding="utf-8")
        else:
            response = httpx.get(url, params=params, timeout=self.timeout_seconds, follow_redirects=True)
            response.raise_for_status()
            text = response.text
            if "No data" in text or not text.strip():
                raise ValueError(f"Stooq returned no data for {symbol}")
            cache_path.write_text(text, encoding="utf-8")
        frame = normalize_prices(pd.read_csv(StringIO(text)), symbol)
        frame["adjustment_status"] = "provider_unspecified"
        return frame


def parse_yahoo_chart(payload: str | dict[str, object]) -> pd.DataFrame:
    data = json.loads(payload) if isinstance(payload, str) else payload
    chart = data.get("chart", {})
    if chart.get("error"):
        raise RuntimeError(f"Yahoo chart error: {chart['error']}")
    results = chart.get("result") or []
    if not results:
        raise ValueError("Yahoo chart response contains no result")
    result = results[0]
    meta = result.get("meta", {})
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators", {})
    quote = (indicators.get("quote") or [{}])[0]
    adjusted = (indicators.get("adjclose") or [{}])[0]
    raw = pd.DataFrame(
        {
            "Date": [datetime.fromtimestamp(value, UTC).date() for value in timestamps],
            "Close": quote.get("close", []),
            "Adj Close": adjusted.get("adjclose", quote.get("close", [])),
            "Volume": quote.get("volume", [None] * len(timestamps)),
        }
    ).dropna(subset=["Close", "Adj Close"])
    return normalize_prices(raw, str(meta.get("symbol", "UNKNOWN")), currency=str(meta.get("currency", "USD")))


class YahooChartPriceProvider:
    """Keyless Yahoo chart adapter; unofficial, personal-research use and no service guarantee."""

    def __init__(self, cache_dir: Path, *, timeout_seconds: float = 30):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        cache_path = self.cache_dir / f"yahoo_{symbol.upper()}_{start:%Y%m%d}_{end:%Y%m%d}.json"
        if cache_path.exists():
            payload = cache_path.read_text(encoding="utf-8")
        else:
            period1 = int(datetime.combine(start, time.min, tzinfo=UTC).timestamp())
            period2 = int(datetime.combine(end + timedelta(days=1), time.min, tzinfo=UTC).timestamp())
            response = httpx.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol.upper()}",
                params={
                    "period1": period1,
                    "period2": period2,
                    "interval": "1d",
                    "events": "div,splits",
                    "includeAdjustedClose": "true",
                },
                headers={"User-Agent": "Mozilla/5.0 AlternativeDataEarningsNowcaster/0.1"},
                timeout=self.timeout_seconds,
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.text
            cache_path.write_text(payload, encoding="utf-8")
        frame = parse_yahoo_chart(payload)
        return frame[frame["trading_date"].between(start, end)].reset_index(drop=True)


class AlphaVantagePriceProvider:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Alpha Vantage API key is required")
        self.api_key = api_key

    def fetch(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        response = httpx.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "TIME_SERIES_DAILY_ADJUSTED",
                "symbol": symbol,
                "outputsize": "full",
                "apikey": self.api_key,
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
        series = payload.get("Time Series (Daily)")
        if not series:
            message = payload.get("Note") or payload.get("Error Message") or "Alpha Vantage returned no series"
            raise RuntimeError(message)
        raw = pd.DataFrame(
            [
                {
                    "Date": observation_date,
                    "Close": values["4. close"],
                    "Adj Close": values["5. adjusted close"],
                    "Volume": values["6. volume"],
                }
                for observation_date, values in series.items()
            ]
        )
        frame = normalize_prices(raw, symbol)
        return frame[frame["trading_date"].between(start, end)].reset_index(drop=True)


def price_rows(frame: pd.DataFrame, *, source: str, source_version: str = "1") -> list[dict[str, object]]:
    created_at = datetime.now(UTC)
    rows: list[dict[str, object]] = []
    for row in frame.itertuples(index=False):
        values = {column: getattr(row, column) for column in PRICE_COLUMNS}
        values.update(
            {
                "price_id": canonical_hash([row.symbol, row.trading_date, source])[:24],
                "source": source,
                "source_version": source_version,
                "created_at": created_at,
            }
        )
        rows.append(values)
    return rows
