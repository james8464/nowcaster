from __future__ import annotations

from datetime import timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from src.live_monitor.types import Direction, MarketQuote, TradeLevelPolicy, TradePlan
from src.strategies.types import canonical_hash

_INTERVAL_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240, "1d": 1_440}


def _floor_tick(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def _ceil_tick(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_CEILING) * tick


def plan_trade_levels(
    quote: MarketQuote,
    direction: Direction,
    *,
    atr: Decimal,
    structural_invalidation: Decimal,
    expected_targets: tuple[Decimal, ...],
    decision_interval: str,
    decision_time,
    policy: TradeLevelPolicy,
) -> TradePlan | None:
    if atr <= 0 or structural_invalidation <= 0 or decision_interval not in _INTERVAL_MINUTES:
        return None
    if any(not target.is_finite() or target <= 0 for target in expected_targets):
        return None

    tick = quote.tick_size
    spread = quote.ask - quote.bid
    chase = policy.maximum_chase_bps / Decimal(10_000)
    if direction is Direction.LONG:
        entry_low = _ceil_tick(quote.ask, tick)
        entry_high = _ceil_tick(quote.ask * (Decimal(1) + chase), tick)
        stop = _floor_tick(min(structural_invalidation, entry_low - policy.atr_multiplier * atr), tick)
        risk = entry_low - stop
        first_threshold = entry_low + risk * policy.minimum_target_1_r
        second_threshold = entry_low + risk * policy.minimum_target_2_r
        candidates = sorted({_ceil_tick(item, tick) for item in expected_targets if item > entry_high})
        target_1 = next((item for item in candidates if item >= first_threshold), None)
        target_2 = next((item for item in candidates if item >= second_threshold and item > (target_1 or 0)), None)
    else:
        entry_high = _floor_tick(quote.bid, tick)
        entry_low = _floor_tick(quote.bid * (Decimal(1) - chase), tick)
        stop = _ceil_tick(max(structural_invalidation, entry_high + policy.atr_multiplier * atr), tick)
        risk = stop - entry_high
        first_threshold = entry_high - risk * policy.minimum_target_1_r
        second_threshold = entry_high - risk * policy.minimum_target_2_r
        candidates = sorted({_floor_tick(item, tick) for item in expected_targets if item < entry_low}, reverse=True)
        target_1 = next((item for item in candidates if item <= first_threshold), None)
        target_2 = next(
            (item for item in candidates if item <= second_threshold and item < (target_1 or Decimal("Infinity"))), None
        )

    if risk <= 0 or risk > atr * policy.maximum_stop_atr:
        return None
    if spread > 0 and risk < spread * policy.minimum_stop_noise_multiple:
        return None
    if target_1 is None or target_2 is None:
        return None

    reward_1 = target_1 - entry_low if direction is Direction.LONG else entry_high - target_1
    reward_2 = target_2 - entry_low if direction is Direction.LONG else entry_high - target_2
    reward_to_risk_1 = reward_1 / risk
    reward_to_risk_2 = reward_2 / risk
    expires_at = decision_time + timedelta(minutes=_INTERVAL_MINUTES[decision_interval] * policy.expires_after_bars)
    identity = {
        "provider": quote.provider,
        "feed": quote.feed,
        "symbol": quote.symbol,
        "decision_interval": decision_interval,
        "direction": direction,
        "decision_time": decision_time,
        "entry_low": str(entry_low),
        "entry_high": str(entry_high),
        "stop": str(stop),
        "target_1": str(target_1),
        "target_2": str(target_2),
    }
    return TradePlan(
        plan_id=canonical_hash(identity),
        provider=quote.provider,
        feed=quote.feed,
        symbol=quote.symbol,
        decision_interval=decision_interval,
        direction=direction,
        decision_time=decision_time,
        expires_at=expires_at,
        entry_low=entry_low,
        entry_high=entry_high,
        stop=stop,
        target_1=target_1,
        target_2=target_2,
        risk_per_unit=risk,
        reward_to_risk_1=reward_to_risk_1,
        reward_to_risk_2=reward_to_risk_2,
        venue_note=(
            "Short execution availability is venue-dependent."
            if quote.provider == "binance" and direction is Direction.SHORT
            else None
        ),
    )


__all__ = ["plan_trade_levels"]
