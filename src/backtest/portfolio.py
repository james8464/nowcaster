from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class PortfolioResult:
    positions: pd.DataFrame
    event_returns: pd.DataFrame
    cumulative_return: float
    gross_cumulative_return: float
    maximum_drawdown: float
    volatility: float
    hit_rate: float
    overlap_rows_removed: int
    caveats: tuple[str, ...]


@dataclass(frozen=True)
class CryptoPortfolioResult:
    positions: pd.DataFrame
    curve: pd.DataFrame
    net_cumulative_return: float
    gross_cumulative_return: float
    maximum_drawdown: float


def maximum_drawdown(returns: pd.Series) -> float:
    wealth = (1 + pd.to_numeric(returns, errors="coerce").fillna(0.0)).cumprod()
    if wealth.empty:
        return float("nan")
    return float((wealth / wealth.cummax() - 1).min())


def run_event_portfolio(
    event_returns: pd.DataFrame,
    *,
    transaction_cost_bps: float = 10,
    slippage_bps: float = 0,
    maximum_position_weight: float = 0.25,
) -> PortfolioResult:
    required = {"company_id", "event_date", "variant_zscore", "abnormal_return"}
    missing = required - set(event_returns.columns)
    if missing:
        raise ValueError(f"Event returns are missing columns: {sorted(missing)}")
    if not 0 < maximum_position_weight <= 0.5:
        raise ValueError("maximum_position_weight must be in (0, 0.5]")
    if transaction_cost_bps < 0 or slippage_bps < 0:
        raise ValueError("Costs cannot be negative")
    clean = event_returns.copy()
    before = len(clean)
    dedupe_columns = ["company_id", "event_date"]
    for optional in ("window_start", "window_end"):
        if optional in clean:
            dedupe_columns.append(optional)
    clean = clean.drop_duplicates(dedupe_columns, keep="first")
    overlap_rows_removed = before - len(clean)
    if "liquidity_status" in clean:
        clean = clean[~clean["liquidity_status"].isin(["ineligible", "failed"])]

    positions: list[dict[str, object]] = []
    portfolio_rows: list[dict[str, object]] = []
    group_columns = ["event_date"]
    for optional in ("window_start", "window_end"):
        if optional in clean:
            group_columns.append(optional)
    for key, group in clean.groupby(group_columns, dropna=False):
        long_leg = group[group["variant_zscore"] > 0]
        short_leg = group[group["variant_zscore"] < 0]
        if long_leg.empty or short_leg.empty:
            continue
        long_weight = min(0.5 / len(long_leg), maximum_position_weight)
        short_weight = -min(0.5 / len(short_leg), maximum_position_weight)
        event_positions: list[dict[str, object]] = []
        for side, leg, weight in (("long", long_leg, long_weight), ("short", short_leg, short_weight)):
            for row in leg.itertuples(index=False):
                item = row._asdict()
                item.update({"side": side, "weight": weight, "contribution": weight * row.abnormal_return})
                event_positions.append(item)
                positions.append(item)
        gross_return = float(sum(float(item["contribution"]) for item in event_positions))
        gross_exposure = float(sum(abs(float(item["weight"])) for item in event_positions))
        round_trip_cost = gross_exposure * 2 * (transaction_cost_bps + slippage_bps) / 10_000
        net_return = gross_return - round_trip_cost
        key_values = key if isinstance(key, tuple) else (key,)
        portfolio_row = dict(zip(group_columns, key_values, strict=True))
        portfolio_row.update(
            {
                "gross_return": gross_return,
                "net_return": net_return,
                "gross_exposure": gross_exposure,
                "round_trip_cost": round_trip_cost,
                "positions": len(event_positions),
            }
        )
        portfolio_rows.append(portfolio_row)
    position_frame = pd.DataFrame(positions)
    returns_frame = pd.DataFrame(portfolio_rows)
    if returns_frame.empty:
        cumulative = gross_cumulative = 0.0
        drawdown = volatility = hit_rate = float("nan")
    else:
        returns_frame = returns_frame.sort_values(group_columns).reset_index(drop=True)
        cumulative = float((1 + returns_frame["net_return"]).prod() - 1)
        gross_cumulative = float((1 + returns_frame["gross_return"]).prod() - 1)
        drawdown = maximum_drawdown(returns_frame["net_return"])
        volatility = float(returns_frame["net_return"].std(ddof=1)) if len(returns_frame) > 1 else float("nan")
        hit_rate = float((returns_frame["net_return"] > 0).mean())
    return PortfolioResult(
        positions=position_frame,
        event_returns=returns_frame,
        cumulative_return=cumulative,
        gross_cumulative_return=gross_cumulative,
        maximum_drawdown=drawdown,
        volatility=volatility,
        hit_rate=hit_rate,
        overlap_rows_removed=overlap_rows_removed,
        caveats=(
            "This is an event-level research simulation, not an executable trading system.",
            "Capacity, borrow availability, taxes, latency, and intraday fills are not modelled.",
        ),
    )


def simulate_crypto_portfolio(
    signals: pd.DataFrame,
    *,
    fee_bps: float = 10,
    slippage_bps: float = 5,
    borrow_bps_annual: float = 300,
    target_volatility: float = 0.15,
    maximum_gross_exposure: float = 1.0,
) -> CryptoPortfolioResult:
    """Simulate one-bar-lagged crypto positions with explicit round-trip costs."""
    required = {
        "signal_id",
        "symbol",
        "decision_date",
        "execution_date",
        "label_end_date",
        "posture",
        "realized_return",
        "forecast_volatility",
    }
    missing = required - set(signals.columns)
    if missing:
        raise ValueError(f"Crypto positions are missing columns: {sorted(missing)}")
    if min(fee_bps, slippage_bps, borrow_bps_annual, target_volatility, maximum_gross_exposure) < 0:
        raise ValueError("Costs, volatility target, and exposure cannot be negative")
    positions = signals.copy().sort_values(["execution_date", "symbol"]).reset_index(drop=True)
    decision = pd.to_datetime(positions["decision_date"])
    execution = pd.to_datetime(positions["execution_date"])
    label_end = pd.to_datetime(positions["label_end_date"])
    if (execution <= decision).any() or (label_end <= execution).any():
        raise ValueError("Every position requires a one-bar execution lag and a later label end")
    direction = positions["posture"].map({"long_research": 1.0, "short_research": -1.0, "abstain": 0.0})
    if direction.isna().any():
        raise ValueError("Unknown research posture")
    horizon_days = (label_end - execution).dt.total_seconds().div(86_400).clip(lower=1)
    horizon_volatility = pd.to_numeric(positions["forecast_volatility"], errors="coerce").clip(lower=1e-6)
    annualized_forecast = horizon_volatility * np.sqrt(365 / horizon_days)
    scale = (target_volatility / annualized_forecast).clip(upper=maximum_gross_exposure)
    positions["weight"] = direction * scale
    positions["gross_exposure"] = positions["weight"].abs()
    positions["turnover"] = 2 * positions["gross_exposure"]
    round_trip_cost = positions["gross_exposure"] * 2 * (fee_bps + slippage_bps) / 10_000
    borrow_cost = (
        (positions["weight"] < 0).astype(float)
        * positions["gross_exposure"]
        * borrow_bps_annual
        / 10_000
        * horizon_days
        / 365
    )
    positions["gross_return"] = positions["weight"] * pd.to_numeric(positions["realized_return"])
    positions["cost"] = round_trip_cost + borrow_cost
    positions["net_return"] = positions["gross_return"] - positions["cost"]
    curve = (
        positions.groupby("label_end_date", as_index=False)
        .agg(
            gross_return=("gross_return", "sum"),
            net_return=("net_return", "sum"),
            gross_exposure=("gross_exposure", "sum"),
            turnover=("turnover", "sum"),
            costs=("cost", "sum"),
        )
        .rename(columns={"label_end_date": "date"})
        .sort_values("date")
        .reset_index(drop=True)
    )
    curve["gross_equity"] = (1 + curve["gross_return"]).cumprod()
    curve["net_equity"] = (1 + curve["net_return"]).cumprod()
    return CryptoPortfolioResult(
        positions=positions,
        curve=curve,
        net_cumulative_return=float(curve["net_equity"].iloc[-1] - 1) if len(curve) else 0,
        gross_cumulative_return=float(curve["gross_equity"].iloc[-1] - 1) if len(curve) else 0,
        maximum_drawdown=maximum_drawdown(curve["net_return"]),
    )
