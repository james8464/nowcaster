from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from src.intraday.contracts import InstrumentSpec
from src.intraday.research import IntradayRound, ResearchCatalog

T = datetime(2026, 10, 6, tzinfo=UTC)
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
        strategy_ids=("opening_range_15", "trend_pullback"),
        train_start=T - timedelta(days=180),
        validation_start=T - timedelta(days=90),
        sealed_start=T - timedelta(days=30),
        sealed_end=T,
    )


def test_catalog_retains_losses_and_rejects_changed_protocol(tmp_path):
    catalog = ResearchCatalog(tmp_path / "round", protocol())
    first = catalog.record_attempt("train", "opening_range_15", "DE30_EUR", D("-12"), D("-20"), 8, "data-a")
    second = catalog.record_attempt("train", "trend_pullback", "DE30_EUR", D("5"), D("-8"), 10, "data-a")
    assert [item.attempt_hash for item in catalog.attempts()] == [first.attempt_hash, second.attempt_hash]
    assert catalog.attempts()[0].net_pnl == D("-12")
    with pytest.raises(ValueError, match="protocol"):
        ResearchCatalog(tmp_path / "round", protocol().model_copy(update={"round_id": "changed"}))


def test_sealed_period_requires_registered_selection_and_can_only_run_once(tmp_path):
    catalog = ResearchCatalog(tmp_path / "round", protocol())
    with pytest.raises(ValueError, match="selection"):
        catalog.record_attempt("sealed", "opening_range_15", "DE30_EUR", D(1), D(1), 1, "sealed-data")
    catalog.record_attempt("train", "opening_range_15", "DE30_EUR", D(9), D(3), 20, "train-data")
    catalog.record_attempt("validation", "opening_range_15", "DE30_EUR", D(2), D(1), 10, "validation-data")
    catalog.freeze_selection("opening_range_15", "DE30_EUR")
    catalog.record_attempt("sealed", "opening_range_15", "DE30_EUR", D(-4), D(-7), 7, "sealed-data")
    with pytest.raises(ValueError, match="once"):
        catalog.record_attempt("sealed", "opening_range_15", "DE30_EUR", D(10), D(10), 7, "sealed-data-two")
    assert len(ResearchCatalog(tmp_path / "round", protocol()).attempts()) == 3


def test_each_instrument_has_separate_frozen_selection_and_one_sealed_exam(tmp_path):
    second = INSTRUMENT.model_copy(update={"broker_symbol": "SPX500_USD", "market": "us500", "quote_currency": "USD"})
    p = protocol().model_copy(update={"instruments": (INSTRUMENT, second)})
    catalog = ResearchCatalog(tmp_path / "round", p)
    for symbol in (INSTRUMENT.broker_symbol, second.broker_symbol):
        catalog.record_attempt("train", "opening_range_15", symbol, D(10), D(5), 20, "train")
        catalog.record_attempt("validation", "opening_range_15", symbol, D(5), D(2), 10, "validation")
        catalog.freeze_selection("opening_range_15", symbol)
        catalog.record_attempt("sealed", "opening_range_15", symbol, D(-2), D(-4), 8, "sealed")
    assert len([a for a in catalog.attempts() if a.stage == "sealed"]) == 2
