from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from src.intraday.contracts import ConfirmedBar, InstrumentSpec
from src.intraday.historical import ReplayCosts
from src.intraday.research import IntradayRound, ResearchCatalog
from src.intraday.workflow import HistoricalSession, evaluate_stage

T = datetime(2026, 10, 6, 8, tzinfo=UTC)
INSTRUMENT = InstrumentSpec(
    provider="oanda_practice",
    broker_symbol="DE30_EUR",
    market="germany40",
    product="cfd",
    quote_currency="EUR",
    point_value=D(1),
)


def protocol():
    return IntradayRound(
        round_id="round-2",
        instruments=(INSTRUMENT,),
        strategy_ids=("opening_range_15",),
        train_start=T - timedelta(days=180),
        validation_start=T - timedelta(days=90),
        sealed_start=T - timedelta(days=30),
        sealed_end=T,
    )


def session(day):
    start = T + timedelta(days=day)
    bars = []
    for n, value in enumerate((100, 100, 101, 104, 106, 118)):
        at = start + timedelta(minutes=n * 5)
        close = D(value)
        bars.append(
            ConfirmedBar(
                instrument=INSTRUMENT,
                start=at,
                end=at + timedelta(minutes=5),
                available_at=at + timedelta(days=1),
                source_key=f"bar-{day}-{n}",
                bid_open=close,
                bid_high=close + (12 if n == 5 else 1),
                bid_low=close - 1,
                bid_close=close,
                ask_open=close + 2,
                ask_high=close + (14 if n == 5 else 3),
                ask_low=close + 1,
                ask_close=close + 2,
            )
        )
    return HistoricalSession(start, start + timedelta(minutes=30), tuple(bars))


def costs():
    return ReplayCosts(
        account_currency="EUR",
        quote_to_account=D(1),
        slippage_points=D(0),
        commission_per_unit=D(0),
        financing_per_unit=D(0),
    )


def test_stage_rejects_future_session_and_records_exploratory_attempt(tmp_path):
    catalog = ResearchCatalog(tmp_path / "catalog", protocol())
    with pytest.raises(ValueError, match="stage window"):
        evaluate_stage(catalog, "train", "opening_range_15", INSTRUMENT, (session(-5),), costs())
    result = evaluate_stage(catalog, "train", "opening_range_15", INSTRUMENT, (session(-179),), costs())
    assert result.closed_trades == 1
    assert result.net_pnl > 0
    assert len(catalog.attempts()) == 1
