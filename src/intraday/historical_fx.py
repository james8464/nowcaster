"""Historical-base FX conversion for exploratory GBP replay only."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from src.intraday.historical import HistoricalFXRate


def load_historical_fx(
    path: Path, *, quote_currency: str, account_currency: str
) -> dict[datetime, HistoricalFXRate]:
    """Read exact-time FX open sides; never substitute account conversion quotes.

    EUR/GBP is direct; GBP/USD is inverted to USD/GBP. Missing timestamps
    remain absent so replay fails closed instead of carrying a stale rate.
    """
    route = {("EUR", "GBP"): ("EUR_GBP", False),
             ("USD", "GBP"): ("GBP_USD", True)}
    if (quote_currency, account_currency) not in route:
        raise ValueError("unsupported historical FX conversion route")
    expected_symbol, inverse = route[(quote_currency, account_currency)]
    with path.open(encoding="utf-8") as source:
        header = json.loads(next(source))
        if (header.get("schema_version"), header.get("source"), header.get("symbol")) != (
            1, "oanda_practice_historical_base", expected_symbol
        ):
            raise ValueError("historical FX identity mismatch")
        rates: dict[datetime, HistoricalFXRate] = {}
        previous = None
        for line in source:
            item = json.loads(line)
            at = datetime.fromisoformat(item["start"].replace("Z", "+00:00"))
            if at.tzinfo is None or at.utcoffset() != timedelta(0) or (previous is not None and at <= previous):
                raise ValueError("historical FX timestamps must be unique chronological UTC")
            bid = Decimal(item["bid"]["open"])
            ask = Decimal(item["ask"]["open"])
            if not bid.is_finite() or not ask.is_finite() or bid <= 0 or ask < bid:
                raise ValueError("invalid historical FX bid/ask")
            rates[at] = HistoricalFXRate(
                gain_factor=Decimal(1) / ask if inverse else bid,
                loss_factor=Decimal(1) / bid if inverse else ask,
            )
            previous = at
    if not rates:
        raise ValueError("historical FX file has no rates")
    return rates
