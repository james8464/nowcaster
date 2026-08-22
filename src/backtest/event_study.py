from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import pandas as pd

from src.backtest.returns import EventWindowError, calculate_event_return
from src.backtest.statistics import summarize_buckets


@dataclass(frozen=True)
class EventStudyResult:
    event_returns: pd.DataFrame
    bucket_summary: pd.DataFrame
    skipped_events: int
    caveats: tuple[str, ...]


def _symbol_prices(prices: pd.DataFrame, symbol: str | None) -> pd.DataFrame | None:
    if symbol is None:
        return None
    selected = prices[prices["symbol"] == symbol].copy()
    if selected.empty:
        return None
    return selected


def run_event_study(
    signals: pd.DataFrame,
    prices: pd.DataFrame,
    windows: Sequence[tuple[int, int]],
    benchmarks: Mapping[str, str],
    *,
    bootstrap_samples: int = 2_000,
    seed: int = 42,
) -> EventStudyResult:
    required_signals = {"signal_id", "company_id", "event_date", "variant", "variant_zscore", "variant_bucket"}
    missing_signals = required_signals - set(signals.columns)
    missing_prices = {"symbol", "trading_date", "adjusted_close"} - set(prices.columns)
    if missing_signals:
        raise ValueError(f"Signals are missing columns: {sorted(missing_signals)}")
    if missing_prices:
        raise ValueError(f"Prices are missing columns: {sorted(missing_prices)}")
    if not windows:
        raise ValueError("At least one event window is required")
    clean_prices = prices.copy()
    clean_prices["trading_date"] = pd.to_datetime(clean_prices["trading_date"], errors="raise").dt.date
    rows: list[dict[str, object]] = []
    skipped = 0
    market = _symbol_prices(clean_prices, benchmarks.get("market"))
    for signal in signals.itertuples(index=False):
        event_date = pd.Timestamp(signal.event_date).date()
        company_prices = _symbol_prices(clean_prices, str(signal.company_id))
        if company_prices is None:
            skipped += len(windows)
            continue
        sector_symbol = benchmarks.get(str(signal.company_id))
        sector_prices = _symbol_prices(clean_prices, sector_symbol)
        for window in windows:
            try:
                event_return = calculate_event_return(company_prices, event_date, window, market)
                sector_return = (
                    calculate_event_return(company_prices, event_date, window, sector_prices)
                    if sector_prices is not None
                    else None
                )
            except EventWindowError:
                skipped += 1
                continue
            row = signal._asdict()
            row.update(
                {
                    "event_date": event_date,
                    "window_start": window[0],
                    "window_end": window[1],
                    "start_date": event_return.start_date,
                    "end_date": event_return.end_date,
                    "raw_return": event_return.raw_return,
                    "benchmark_return": event_return.benchmark_return,
                    "abnormal_return": event_return.abnormal_return
                    if event_return.abnormal_return is not None
                    else event_return.raw_return,
                    "sector_return": sector_return.benchmark_return if sector_return is not None else None,
                    "sector_adjusted_return": sector_return.abnormal_return if sector_return is not None else None,
                    "liquidity_status": row.get("liquidity_status", "not_assessed"),
                }
            )
            rows.append(row)
    frame = pd.DataFrame(rows)
    summary = (
        summarize_buckets(frame, bootstrap_samples=bootstrap_samples, seed=seed) if not frame.empty else pd.DataFrame()
    )
    return EventStudyResult(
        event_returns=frame,
        bucket_summary=summary,
        skipped_events=skipped,
        caveats=(
            "Event dates may be SEC filing-date proxies where issuer calendars are unavailable.",
            "Results are exploratory and do not establish tradable alpha or future profitability.",
            "Bootstrap intervals do not remove selection bias, overlapping-event dependence, or multiple testing.",
        ),
    )
