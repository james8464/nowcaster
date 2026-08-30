"""Execution evidence from authenticated full books, never incremental depth totals."""

from __future__ import annotations

from datetime import UTC
from decimal import ROUND_DOWN, Decimal

from src.contextual.eligibility import EligibilityInputs
from src.live_monitor.types import DepthLevel, MarketDepth, MarketQuote, MarketStatusEvent


def _sweep(levels: tuple[DepthLevel, ...], quantity: Decimal) -> Decimal:
    remaining, notional = quantity, Decimal(0)
    for level in levels:
        take = min(remaining, level.size)
        remaining -= take
        notional += take * level.price
        if remaining == 0:
            return notional / quantity
    raise ValueError("verified book cannot fill the research probe")


def observed_execution_inputs(
    inputs: EligibilityInputs,
    quote: MarketQuote,
    depth: MarketDepth,
    rules: MarketStatusEvent,
    *,
    probe_notional: float,
) -> EligibilityInputs:
    """Test a hypothetical limit-sized probe on both sides; this never places an order."""

    if not depth.snapshot_verified or rules.status != "instrument_rules":
        raise ValueError("full depth and instrument rules are required")
    for event in (quote, depth, rules):
        if (event.provider, event.feed, event.symbol) != (inputs.provider, inputs.feed, inputs.symbol):
            raise ValueError("execution evidence identity mismatch")
        if event.processed_at is None or event.processed_at > inputs.as_of:
            raise ValueError("execution evidence was not available at the decision")
    if abs(depth.bids[0].price - quote.bid) > quote.tick_size or abs(depth.asks[0].price - quote.ask) > quote.tick_size:
        raise ValueError("quote and full-depth top of book disagree")
    filters = rules.details.get("filters")
    if not isinstance(filters, list) or len(filters) > 100 or not all(isinstance(item, dict) for item in filters):
        raise ValueError("instrument filters are unavailable")
    lots = [item for item in filters if item.get("filterType") == "LOT_SIZE"]
    notional_filters = [item for item in filters if item.get("filterType") in {"MIN_NOTIONAL", "NOTIONAL"}]
    if len(lots) != 1 or not notional_filters:
        raise ValueError("quantity and notional rules are required")
    minimum, maximum, step = (Decimal(str(lots[0][name])) for name in ("minQty", "maxQty", "stepSize"))
    if not all(value.is_finite() and value > 0 for value in (minimum, maximum, step)) or maximum < minimum:
        raise ValueError("invalid quantity filter")
    midpoint = (quote.bid + quote.ask) / 2
    quantity = (Decimal(str(probe_notional)) / midpoint / step).to_integral_value(rounding=ROUND_DOWN) * step
    lot_valid = minimum <= quantity <= maximum and quantity > 0
    for item in notional_filters:
        lower = Decimal(str(item["minNotional"]))
        upper = Decimal(str(item.get("maxNotional", 0)))
        if not lower.is_finite() or not upper.is_finite() or lower < 0 or upper < 0:
            raise ValueError("invalid notional filter")
        lot_valid = lot_valid and quantity * quote.bid >= lower and (upper == 0 or quantity * quote.ask <= upper)
    if quantity <= 0:
        raise ValueError("probe is below one quantity increment")
    buy_price, sell_price = _sweep(depth.asks, quantity), _sweep(depth.bids, quantity)
    impact = max(buy_price / quote.ask - 1, 1 - sell_price / quote.bid, Decimal(0)) * 10_000
    # The weaker side constrains capacity, including the potential exit.
    depth_notional = min(sum(item.price * item.size for item in levels) for levels in (depth.bids, depth.asks))
    updates = {
        "last_price": float(quote.last),
        "tick_size": float(quote.tick_size),
        "lot_size_valid": lot_valid,
        "trading_status": "active" if rules.details.get("tradable") is True else "inactive",
        "spread_bps": float((quote.ask - quote.bid) / midpoint * 10_000),
        "depth_notional": float(depth_notional),
        "estimated_price_impact_bps": float(impact),
        "participation_rate": float(quantity * midpoint) / inputs.median_notional_volume,
        "data_through": min(inputs.data_through, quote.provider_time, depth.provider_time).astimezone(UTC),
        "liquidity_grade": "observed",
    }
    return EligibilityInputs.model_validate({**inputs.model_dump(), **updates})
