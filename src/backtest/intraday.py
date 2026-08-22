from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace

import numpy as np
import pandas as pd

from src.backtest.costs import calculate_carry_cost
from src.backtest.execution import (
    ExecutionAssumptions,
    ExecutionResult,
    Fill,
    OrderIntent,
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


def adapt_strategy_signal_frame(
    signals: pd.DataFrame,
    *,
    strategy_id: str | None = None,
    symbol: str | None = None,
) -> pd.DataFrame:
    """Attach the identity omitted by Task 3's symbol-local signal contract."""

    result = signals.copy()
    for column, supplied in (("strategy_id", strategy_id), ("symbol", symbol)):
        if column not in result:
            if supplied is None or not supplied.strip():
                raise ValueError(f"{column} is required for a StrategySignalFrame without that column")
            result[column] = supplied
        elif supplied is not None and not (result[column].astype(str) == supplied).all():
            raise ValueError(f"supplied {column} conflicts with the StrategySignalFrame")
    return result


def _validated_signals(
    signals: pd.DataFrame,
    *,
    strategy_id: str | None = None,
    symbol: str | None = None,
) -> pd.DataFrame:
    signals = adapt_strategy_signal_frame(signals, strategy_id=strategy_id, symbol=symbol)
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


def _lot_floor(quantity: float, lot_size: float) -> float:
    return float(round(math.floor((quantity + 1e-12) / lot_size) * lot_size, 12))


def _scaled_orders(
    orders: list[OrderIntent],
    fraction: float,
    lot_size: float,
) -> list[OrderIntent]:
    scaled: list[OrderIntent] = []
    for order in orders:
        quantity = _lot_floor(order.quantity * fraction, lot_size)
        if quantity > 0:
            scaled.append(replace(order, quantity=quantity))
    return scaled


def _projected_risk_is_valid(
    execution: ExecutionResult,
    strategy_symbols: dict[str, set[str]],
    cash: float,
    mark_prices: dict[str, float],
    risk: RiskLimits,
) -> tuple[bool, float]:
    projected_cash = cash + sum(_cash_change(fill) for fill in execution.fills)
    equity = projected_cash + sum(quantity * mark_prices[symbol] for symbol, quantity in execution.positions.items())
    if equity <= 0 or projected_cash < -1e-9:
        return False, projected_cash
    notionals = {symbol: quantity * mark_prices[symbol] for symbol, quantity in execution.positions.items()}
    tolerance = 1e-9
    if sum(abs(value) for value in notionals.values()) / equity > risk.maximum_gross_exposure + tolerance:
        return False, projected_cash
    if abs(sum(notionals.values())) / equity > risk.maximum_net_exposure + tolerance:
        return False, projected_cash
    if any(abs(value) / equity > risk.maximum_asset_exposure + tolerance for value in notionals.values()):
        return False, projected_cash
    for symbols in strategy_symbols.values():
        strategy_gross = sum(abs(notionals.get(symbol, 0.0)) for symbol in symbols) / equity
        if strategy_gross > risk.maximum_strategy_exposure + tolerance:
            return False, projected_cash
    return True, projected_cash


def _execute_atomic_batch(
    bars: pd.DataFrame,
    orders: list[OrderIntent],
    assumptions: ExecutionAssumptions,
    positions: dict[str, float],
    cash: float,
    mark_prices: dict[str, float],
    risk: RiskLimits,
    strategy_symbols: dict[str, set[str]],
) -> tuple[tuple[Fill, ...], list[dict[str, object]], dict[str, float], float]:
    if not orders:
        return (), [], positions.copy(), cash
    local = replace(
        assumptions,
        latency=pd.Timedelta(0).to_pytimedelta(),
        flatten_at_session_end=False,
        session_close=None,
    )
    preview = run_execution(bars, orders, local, initial_positions=positions)
    filled_by_order = {fill.order_id: fill.quantity for fill in preview.fills}
    executable_fraction = min(filled_by_order.get(order.order_id, 0.0) / order.quantity for order in orders)
    if executable_fraction <= 0:
        rejected = [asdict(item) for item in preview.rejections]
        rejected.extend(
            {
                "order_id": order.order_id,
                "strategy_id": order.strategy_id,
                "symbol": order.symbol,
                "reason": "dependent_leg_not_executable",
            }
            for order in orders
            if order.order_id in filled_by_order
        )
        return (), rejected, positions.copy(), cash

    fraction = executable_fraction
    for _attempt in range(100):
        candidates = _scaled_orders(orders, fraction, assumptions.lot_size)
        if len(candidates) != len(orders):
            break
        execution = run_execution(bars, candidates, local, initial_positions=positions)
        fully_executable = len(execution.fills) == len(candidates) and all(
            fill.status == "filled" for fill in execution.fills
        )
        valid, projected_cash = _projected_risk_is_valid(execution, strategy_symbols, cash, mark_prices, risk)
        if fully_executable and valid:
            rejected = []
            if fraction + 1e-12 < 1:
                rejected = [
                    {
                        "order_id": order.order_id,
                        "strategy_id": order.strategy_id,
                        "symbol": order.symbol,
                        "reason": "dependent_legs_rescaled",
                    }
                    for order in orders
                ]
            return execution.fills, rejected, execution.positions, projected_cash
        fraction *= 0.99

    rejected = [
        {
            "order_id": order.order_id,
            "strategy_id": order.strategy_id,
            "symbol": order.symbol,
            "reason": "projected_risk_limit",
        }
        for order in orders
    ]
    return (), rejected, positions.copy(), cash


def _execute_session_close_batch(
    bars: pd.DataFrame,
    assumptions: ExecutionAssumptions,
    positions: dict[str, float],
    used_capacity: dict[tuple[str, pd.Timestamp], float],
    cash: float,
    mark_prices: dict[str, float],
    risk: RiskLimits,
    strategy_symbols: dict[str, set[str]],
) -> tuple[tuple[Fill, ...], list[dict[str, object]], dict[str, float], float]:
    execution = run_execution(
        bars,
        [],
        replace(assumptions, latency=pd.Timedelta(0).to_pytimedelta()),
        initial_positions=positions,
        initial_used_capacity=used_capacity,
    )
    rejected = [asdict(item) for item in execution.rejections]
    if not execution.fills:
        return (), rejected, positions.copy(), cash
    valid, projected_cash = _projected_risk_is_valid(execution, strategy_symbols, cash, mark_prices, risk)
    if valid:
        return execution.fills, rejected, execution.positions, projected_cash
    rejected.extend(
        {
            "order_id": fill.order_id,
            "strategy_id": fill.strategy_id,
            "symbol": fill.symbol,
            "reason": "session_close_batch_projected_risk_limit",
        }
        for fill in execution.fills
    )
    return (), rejected, positions.copy(), cash


def run_intraday_backtest(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    assumptions: ExecutionAssumptions,
    risk: RiskLimits,
    *,
    strategy_id: str | None = None,
    symbol: str | None = None,
) -> IntradayBacktestResult:
    ordered_bars = _validated_bars(bars)
    decisions = _validated_signals(signals, strategy_id=strategy_id, symbol=symbol)
    unknown_symbols = set(decisions["symbol"]) - set(ordered_bars["symbol"])
    if unknown_symbols:
        raise ValueError(f"Signals reference symbols without bars: {sorted(unknown_symbols)}")

    cash = risk.initial_cash
    prior_equity = risk.initial_cash
    positions: dict[str, float] = {}
    last_prices: dict[str, float] = {}
    states: dict[tuple[str, str], float] = {}
    strategy_symbols: dict[str, set[str]] = {}
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
            signal_strategy = str(signal["strategy_id"])
            signal_symbol = str(signal["symbol"])
            states[(signal_strategy, signal_symbol)] = float(signal["signal"] * signal["strength"])
            strategy_symbols.setdefault(signal_strategy, set()).add(signal_symbol)
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
        orders: list[OrderIntent] = []
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
            orders.append(
                OrderIntent(
                    order_id=f"portfolio:{order_sequence:08d}",
                    strategy_id=strategy_id or "portfolio_rebalance",
                    symbol=symbol,
                    decision_timestamp=decision_timestamp,
                    side=side,
                    quantity=abs(delta),
                    position_effect="open" if side == "sell" and desired_quantities[symbol] < 0 else "auto",
                )
            )
            order_sequence += 1

        batch_fills, batch_rejections, projected_positions, projected_cash = _execute_atomic_batch(
            current,
            orders,
            assumptions,
            positions,
            cash,
            mark_prices,
            risk,
            strategy_symbols,
        )
        rejection_rows.extend(batch_rejections)
        positions = projected_positions
        cash = projected_cash
        open_filled_capacity: dict[tuple[str, pd.Timestamp], float] = {}
        for fill in batch_fills:
            fills.append(fill)
            bar_turnover += fill.notional
            bar_costs += fill.total_cost
            key = (fill.symbol, pd.Timestamp(fill.execution_timestamp))
            open_filled_capacity[key] = open_filled_capacity.get(key, 0.0) + fill.quantity

        close_prices = {str(row.symbol): float(row.close) for row in current.itertuples(index=False)}
        last_prices.update(close_prices)
        carry_total = 0.0
        for symbol, quantity in positions.items():
            carry_total += calculate_carry_cost(quantity, last_prices[symbol], assumptions.costs).total
        cash -= carry_total
        bar_costs += carry_total

        if assumptions.flatten_at_session_end:
            close_fills, close_rejections, projected_positions, projected_cash = _execute_session_close_batch(
                current,
                assumptions,
                positions,
                open_filled_capacity,
                cash,
                last_prices,
                risk,
                strategy_symbols,
            )
            rejection_rows.extend(close_rejections)
            positions = projected_positions
            cash = projected_cash
            for fill in close_fills:
                fills.append(fill)
                bar_turnover += fill.notional
                bar_costs += fill.total_cost

        equity = cash + sum(quantity * last_prices[symbol] for symbol, quantity in positions.items())
        net_return = equity / prior_equity - 1 if prior_equity else float("nan")
        cost_return = bar_costs / prior_equity if prior_equity else float("nan")
        gross_return = net_return + cost_return
        gross_notional = sum(abs(quantity * last_prices[symbol]) for symbol, quantity in positions.items())
        net_notional = sum(quantity * last_prices[symbol] for symbol, quantity in positions.items())
        curve_rows.append(
            {
                "timestamp": pd.Timestamp(current["close_timestamp"].max()),
                "cash": cash,
                "equity": equity,
                "net_return": net_return,
                "gross_return": gross_return,
                "cost_return": cost_return,
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


__all__ = ["IntradayBacktestResult", "RiskLimits", "adapt_strategy_signal_frame", "run_intraday_backtest"]
