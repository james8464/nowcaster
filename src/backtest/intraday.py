from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace

import numpy as np
import pandas as pd

from src.backtest.costs import calculate_carry_cost
from src.backtest.execution import (
    ExecutionAssumptions,
    Fill,
    OrderIntent,
    _make_fill,
    _validated_bars,
    run_execution,
)
from src.backtest.metrics import BacktestMetrics, calculate_backtest_metrics


@dataclass(frozen=True, slots=True)
class RiskLimits:
    initial_cash: float = 100_000.0
    maximum_gross_exposure: float = 1.0
    maximum_net_exposure: float = 1.0
    maximum_asset_exposure: float = 1.0
    maximum_strategy_exposure: float = 1.0
    target_volatility: float | None = None
    volatility_lookback: int = 20
    minimum_volatility_observations: int = 5
    periods_per_year: int = 252

    def __post_init__(self) -> None:
        if self.initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        limits = (
            self.maximum_gross_exposure,
            self.maximum_net_exposure,
            self.maximum_asset_exposure,
            self.maximum_strategy_exposure,
        )
        if any(not 0 < value <= 1 for value in limits):
            raise ValueError("exposure limits must be in (0, 1]")
        if self.target_volatility is not None and self.target_volatility <= 0:
            raise ValueError("target_volatility must be positive")
        if self.volatility_lookback < 2 or self.minimum_volatility_observations < 2:
            raise ValueError("volatility windows require at least two observations")
        if self.minimum_volatility_observations > self.volatility_lookback:
            raise ValueError("minimum volatility observations cannot exceed the lookback")
        if self.periods_per_year <= 0:
            raise ValueError("periods_per_year must be positive")


@dataclass(frozen=True, slots=True)
class IntradayBacktestResult:
    equity_curve: pd.DataFrame
    trade_ledger: pd.DataFrame
    rejection_ledger: pd.DataFrame
    metrics: BacktestMetrics


_TRADE_COLUMNS = [field.name for field in Fill.__dataclass_fields__.values()]
_REJECTION_COLUMNS = ["order_id", "strategy_id", "symbol", "reason"]


def _validated_signals(signals: pd.DataFrame) -> pd.DataFrame:
    required = {"strategy_id", "symbol", "decision_timestamp", "data_through", "signal", "strength"}
    missing = required - set(signals.columns)
    if missing:
        raise ValueError(f"Strategy signals are missing columns: {sorted(missing)}")
    result = signals.copy()
    result["strategy_id"] = result["strategy_id"].astype(str)
    result["symbol"] = result["symbol"].astype(str).str.upper()
    result["decision_timestamp"] = pd.to_datetime(result["decision_timestamp"], utc=True, errors="coerce")
    result["data_through"] = pd.to_datetime(result["data_through"], utc=True, errors="coerce")
    if result[["decision_timestamp", "data_through"]].isna().any().any():
        raise ValueError("signal timestamps must be valid")
    if (result["decision_timestamp"] < result["data_through"]).any():
        raise ValueError("a decision cannot precede the finalized data it uses")
    result["signal"] = pd.to_numeric(result["signal"], errors="coerce")
    result["strength"] = pd.to_numeric(result["strength"], errors="coerce")
    if result[["signal", "strength"]].isna().any().any():
        raise ValueError("signal and strength must be numeric")
    if not result["signal"].isin((-1, 0, 1)).all():
        raise ValueError("signals must be -1, 0, or 1")
    if not result["strength"].between(0, 1).all():
        raise ValueError("signal strength must be in [0, 1]")
    if result.duplicated(["strategy_id", "symbol", "decision_timestamp"]).any():
        raise ValueError("strategy decisions must be unique per symbol and timestamp")
    return result.sort_values(["decision_timestamp", "strategy_id", "symbol"], kind="stable").reset_index(drop=True)


def _volatility_state(returns: list[float], risk: RiskLimits) -> tuple[float, float]:
    if risk.target_volatility is None or len(returns) < risk.minimum_volatility_observations:
        return float("nan"), 1.0
    window = np.asarray(returns[-risk.volatility_lookback :], dtype=float)
    estimate = float(np.std(window, ddof=1) * math.sqrt(risk.periods_per_year))
    if not math.isfinite(estimate) or estimate <= 0:
        return estimate, 1.0
    return estimate, min(risk.target_volatility / estimate, 1.0)


def _target_fractions(
    states: dict[tuple[str, str], float],
    risk: RiskLimits,
    volatility_scale: float,
) -> dict[str, float]:
    contributions: dict[tuple[str, str], float] = {}
    by_strategy: dict[str, list[tuple[str, float]]] = {}
    for (strategy, symbol), value in states.items():
        by_strategy.setdefault(strategy, []).append((symbol, value))
    for strategy, values in sorted(by_strategy.items()):
        gross = sum(abs(value) for _, value in values)
        scale = min(risk.maximum_strategy_exposure / gross, 1.0) if gross else 0.0
        for symbol, value in values:
            contributions[(strategy, symbol)] = value * scale
    targets: dict[str, float] = {}
    for (_strategy, symbol), value in contributions.items():
        targets[symbol] = targets.get(symbol, 0.0) + value
    targets = {
        symbol: float(np.clip(value, -risk.maximum_asset_exposure, risk.maximum_asset_exposure)) * volatility_scale
        for symbol, value in targets.items()
    }
    gross = sum(abs(value) for value in targets.values())
    net = abs(sum(targets.values()))
    scale = 1.0
    if gross > risk.maximum_gross_exposure:
        scale = min(scale, risk.maximum_gross_exposure / gross)
    if net > risk.maximum_net_exposure:
        scale = min(scale, risk.maximum_net_exposure / net)
    return {symbol: value * scale for symbol, value in targets.items()}


def _cash_change(fill: Fill) -> float:
    explicit_cost = fill.fee + fill.commission
    return -fill.notional - explicit_cost if fill.side == "buy" else fill.notional - explicit_cost


def _affordable_fill(
    bar: pd.Series,
    order: OrderIntent,
    assumptions: ExecutionAssumptions,
    cash: float,
    position_quantity: float,
) -> tuple[Fill | None, list[dict[str, object]]]:
    local = replace(
        assumptions, latency=pd.Timedelta(0).to_pytimedelta(), flatten_at_session_end=False, session_label=None
    )
    quantity = order.quantity
    rejections: list[dict[str, object]] = []
    while quantity + 1e-12 >= assumptions.lot_size:
        candidate = replace(order, quantity=quantity)
        execution = run_execution(
            pd.DataFrame([bar]),
            [candidate],
            local,
            initial_positions={order.symbol: position_quantity},
        )
        if not execution.fills:
            rejections.extend(asdict(item) for item in execution.rejections)
            return None, rejections
        fill = execution.fills[0]
        if fill.side != "buy" or -_cash_change(fill) <= cash + 1e-9:
            return fill, rejections
        quantity = round(quantity - assumptions.lot_size, 12)
    rejections.append(
        {
            "order_id": order.order_id,
            "strategy_id": order.strategy_id,
            "symbol": order.symbol,
            "reason": "insufficient_cash",
        }
    )
    return None, rejections


def _session_end_for_symbol(
    bars: pd.DataFrame,
    index: int,
    assumptions: ExecutionAssumptions,
) -> bool:
    if not assumptions.flatten_at_session_end or assumptions.session_label is None:
        return False
    row = bars.iloc[index]
    later = bars.iloc[index + 1 :]
    next_rows = later[later["symbol"] == row["symbol"]]
    if next_rows.empty:
        return False
    return assumptions.session_label(row) != assumptions.session_label(next_rows.iloc[0])


def run_intraday_backtest(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    assumptions: ExecutionAssumptions,
    risk: RiskLimits,
) -> IntradayBacktestResult:
    ordered_bars = _validated_bars(bars)
    decisions = _validated_signals(signals)
    unknown_symbols = set(decisions["symbol"]) - set(ordered_bars["symbol"])
    if unknown_symbols:
        raise ValueError(f"Signals reference symbols without bars: {sorted(unknown_symbols)}")

    cash = risk.initial_cash
    prior_equity = risk.initial_cash
    positions: dict[str, float] = {}
    last_prices: dict[str, float] = {}
    states: dict[tuple[str, str], float] = {}
    consumed: set[int] = set()
    returns: list[float] = []
    fills: list[Fill] = []
    rejection_rows: list[dict[str, object]] = []
    curve_rows: list[dict[str, object]] = []
    order_sequence = 0

    grouped = list(ordered_bars.groupby("open_timestamp", sort=True))
    for timestamp, group in grouped:
        current = group.sort_values("symbol", kind="stable")
        open_prices = {str(row.symbol): float(row.open) for row in current.itertuples(index=False)}
        mark_prices = {**last_prices, **open_prices}
        pretrade_equity = cash + sum(quantity * mark_prices[symbol] for symbol, quantity in positions.items())

        for index, signal in decisions.iterrows():
            if index in consumed:
                continue
            eligible_at = signal["decision_timestamp"] + assumptions.latency
            if timestamp < eligible_at:
                continue
            symbol_bar = current[current["symbol"] == signal["symbol"]]
            if symbol_bar.empty or pd.Timestamp(symbol_bar.iloc[0]["close_timestamp"]) <= signal["decision_timestamp"]:
                continue
            states[(str(signal["strategy_id"]), str(signal["symbol"]))] = float(signal["signal"] * signal["strength"])
            consumed.add(index)

        volatility_estimate, volatility_scale = _volatility_state(returns, risk)
        target_fractions = _target_fractions(states, risk, volatility_scale)
        desired_quantities = {
            symbol: pretrade_equity * target_fractions.get(symbol, 0.0) / open_prices[symbol] for symbol in open_prices
        }
        deltas = {
            symbol: desired_quantities.get(symbol, 0.0) - positions.get(symbol, 0.0)
            for symbol in sorted(set(positions) | set(desired_quantities))
            if symbol in open_prices
        }
        ordered_deltas = sorted(deltas.items(), key=lambda item: (item[1] > 0, item[0]))
        bar_turnover = 0.0
        bar_costs = 0.0
        for symbol, delta in ordered_deltas:
            if abs(delta) + 1e-12 < assumptions.lot_size:
                continue
            side = "buy" if delta > 0 else "sell"
            strategy_ids = sorted(
                strategy for (strategy, item_symbol), value in states.items() if item_symbol == symbol and value
            )
            strategy_id = strategy_ids[0] if len(strategy_ids) == 1 else "netted:" + "+".join(strategy_ids)
            latest_decision = decisions.loc[
                (decisions["symbol"] == symbol) & decisions.index.isin(consumed), "decision_timestamp"
            ].max()
            decision_timestamp = pd.Timestamp(timestamp if pd.isna(latest_decision) else latest_decision)
            order = OrderIntent(
                order_id=f"portfolio:{order_sequence:08d}",
                strategy_id=strategy_id or "portfolio_rebalance",
                symbol=symbol,
                decision_timestamp=decision_timestamp,
                side=side,
                quantity=abs(delta),
                position_effect="open" if side == "sell" and desired_quantities[symbol] < 0 else "auto",
            )
            order_sequence += 1
            bar = current[current["symbol"] == symbol].iloc[0]
            fill, rejected = _affordable_fill(bar, order, assumptions, cash, positions.get(symbol, 0.0))
            rejection_rows.extend(rejected)
            if fill is None:
                continue
            fills.append(fill)
            cash += _cash_change(fill)
            positions[symbol] = positions.get(symbol, 0.0) + fill.quantity * (1 if fill.side == "buy" else -1)
            bar_turnover += fill.notional
            bar_costs += fill.total_cost

        for index, bar in current.iterrows():
            symbol = str(bar["symbol"])
            if (
                _session_end_for_symbol(ordered_bars, int(index), assumptions)
                and abs(positions.get(symbol, 0.0)) > 1e-12
            ):
                quantity = positions[symbol]
                side = "sell" if quantity > 0 else "buy"
                flatten_order = OrderIntent(
                    order_id=f"session-flatten:{order_sequence:08d}",
                    strategy_id="session_flatten",
                    symbol=symbol,
                    decision_timestamp=pd.Timestamp(bar["open_timestamp"]),
                    side=side,
                    quantity=abs(quantity),
                    order_type="timed_exit",
                    position_effect="close",
                )
                order_sequence += 1
                flatten_fill = _make_fill(
                    flatten_order,
                    bar,
                    assumptions,
                    abs(quantity),
                    float(bar["close"]),
                    "scheduled_session_flatten",
                    execution_timestamp=pd.Timestamp(bar["close_timestamp"]),
                    order_type="session_flatten",
                )
                fills.append(flatten_fill)
                cash += _cash_change(flatten_fill)
                positions[symbol] = 0.0
                bar_turnover += flatten_fill.notional
                bar_costs += flatten_fill.total_cost

        close_prices = {str(row.symbol): float(row.close) for row in current.itertuples(index=False)}
        last_prices.update(close_prices)
        carry_total = 0.0
        for symbol, quantity in positions.items():
            carry_total += calculate_carry_cost(quantity, last_prices[symbol], assumptions.costs).total
        cash -= carry_total
        bar_costs += carry_total
        equity = cash + sum(quantity * last_prices[symbol] for symbol, quantity in positions.items())
        net_return = equity / prior_equity - 1 if prior_equity else float("nan")
        gross_notional = sum(abs(quantity * last_prices[symbol]) for symbol, quantity in positions.items())
        net_notional = sum(quantity * last_prices[symbol] for symbol, quantity in positions.items())
        curve_rows.append(
            {
                "timestamp": pd.Timestamp(current["close_timestamp"].max()),
                "cash": cash,
                "equity": equity,
                "net_return": net_return,
                "gross_exposure": gross_notional / equity if equity else float("nan"),
                "net_exposure": net_notional / equity if equity else float("nan"),
                "turnover": bar_turnover / pretrade_equity if pretrade_equity else float("nan"),
                "costs": bar_costs,
                "volatility_estimate": volatility_estimate,
                "volatility_scale": volatility_scale,
            }
        )
        returns.append(net_return)
        prior_equity = equity

    for index, signal in decisions.iterrows():
        if index not in consumed:
            rejection_rows.append(
                {
                    "order_id": f"signal:{signal['strategy_id']}:{signal['symbol']}:{signal['decision_timestamp']}",
                    "strategy_id": signal["strategy_id"],
                    "symbol": signal["symbol"],
                    "reason": "no_actionable_bar_after_latency",
                }
            )
    curve = pd.DataFrame(curve_rows)
    trade_ledger = pd.DataFrame([asdict(fill) for fill in fills], columns=_TRADE_COLUMNS)
    rejection_ledger = pd.DataFrame(rejection_rows, columns=_REJECTION_COLUMNS)
    metrics = calculate_backtest_metrics(curve, trade_ledger, periods_per_year=risk.periods_per_year)
    return IntradayBacktestResult(curve, trade_ledger, rejection_ledger, metrics)


__all__ = ["IntradayBacktestResult", "RiskLimits", "run_intraday_backtest"]
