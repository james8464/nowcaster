from __future__ import annotations

from src.backtest.execution import OrderIntent
from src.strategies.types import canonical_hash
from src.trading.types import TradingEnvironment

_PREFIX = {
    TradingEnvironment.SHADOW: "nc1s-",
    TradingEnvironment.PAPER: "nc1p-",
    TradingEnvironment.LIVE: "nc1l-",
}


def client_order_id(
    intent: OrderIntent,
    *,
    account_suffix: str,
    environment: TradingEnvironment,
) -> str:
    """Return a versioned, secret-free identity for one logical broker effect."""

    account_suffix = account_suffix.strip()
    if len(account_suffix) < 4:
        raise ValueError("account suffix must contain at least four characters")
    payload = {
        "schema_version": 1,
        "environment": environment.value,
        "account_suffix": account_suffix,
        "order_id": intent.order_id,
        "strategy_id": intent.strategy_id,
        "symbol": intent.symbol,
        "decision_timestamp": intent.decision_timestamp,
        "side": intent.side,
        "quantity": intent.quantity,
        "order_type": intent.order_type,
        "stop_price": intent.stop_price,
        "target_price": intent.target_price,
        "position_effect": intent.position_effect,
        "liquidity": intent.liquidity,
        "decision_hash": intent.decision_hash,
        "source_decision_hashes": intent.source_decision_hashes,
    }
    return _PREFIX[environment] + canonical_hash(payload)[:40]


__all__ = ["client_order_id"]
