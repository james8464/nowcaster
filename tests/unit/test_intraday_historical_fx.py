import json
from datetime import UTC, datetime
from decimal import Decimal as D

import pytest

from src.intraday.historical_fx import load_historical_fx


def write_fx(tmp_path, symbol, rows):
    path = tmp_path / f"{symbol}.jsonl"
    header = {"schema_version": 1, "source": "oanda_practice_historical_base", "symbol": symbol,
              "requested_start": "2026-04-01T00:00:00+00:00",
              "requested_end": "2026-10-01T00:00:00+00:00"}
    path.write_text("\n".join(json.dumps(item) for item in (header, *rows)) + "\n")
    return path


def row(at, bid, ask):
    return {"start": at, "bid": {"open": bid}, "ask": {"open": ask}}


def test_load_direct_and_inverse_historical_fx_use_conservative_sides(tmp_path):
    at = datetime(2026, 4, 1, tzinfo=UTC)
    eur = load_historical_fx(write_fx(tmp_path, "EUR_GBP", [row(at.isoformat(), "0.85", "0.86")]),
                             quote_currency="EUR", account_currency="GBP")
    usd = load_historical_fx(write_fx(tmp_path, "GBP_USD", [row(at.isoformat(), "1.25", "1.26")]),
                             quote_currency="USD", account_currency="GBP")
    assert eur[at].gain_factor == D("0.85")
    assert eur[at].loss_factor == D("0.86")
    assert usd[at].gain_factor == D(1) / D("1.26")
    assert usd[at].loss_factor == D(1) / D("1.25")


def test_load_historical_fx_rejects_mislabelled_or_duplicate_or_invalid_rows(tmp_path):
    at = "2026-04-01T00:00:00+00:00"
    good = row(at, "0.85", "0.86")
    for symbol, rows in (("GBP_USD", [good]), ("EUR_GBP", [good, good]),
                         ("EUR_GBP", [row(at, "0.87", "0.86")])):
        with pytest.raises(ValueError):
            load_historical_fx(write_fx(tmp_path, symbol, rows),
                               quote_currency="EUR", account_currency="GBP")
