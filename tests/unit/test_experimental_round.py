"""An experiment's identity and cost evidence cannot be changed after registration."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.intraday.contracts import InstrumentSpec
from src.intraday.experimental_round import (
    ExperimentalRoundManifest,
    load_experimental_round,
    register_experimental_round,
)
from src.intraday.paper import PaperExecutionCosts


OPEN = datetime(2026, 10, 20, 7, tzinfo=UTC)
ACCOUNT_HASH = "a" * 64
CODE_HASH = "b" * 64


def manifest(**changes):
    values = dict(
        round_id="de30-2026-10-20-a", cohort_id="de30-or15-long-v1",
        account_feed_hash=ACCOUNT_HASH,
        instrument=InstrumentSpec(provider="oanda_practice", broker_symbol="DE30_EUR", market="germany40", product="cfd", quote_currency="EUR", point_value=Decimal("1")),
        strategy_id="opening_range_15", direction="long",
        session_open=OPEN, session_close=OPEN + timedelta(hours=13),
        calendar_hash="c" * 64,
        costs=PaperExecutionCosts(verified=True, slippage_points=Decimal("0.5"), commission_per_unit=Decimal("0.1"), financing_per_unit=Decimal("0.01")),
        cost_source="https://www.oanda.com/uk-en/trading/fees/", cost_source_hash="d" * 64,
        verified_at=OPEN - timedelta(days=1), account_currency="GBP",
        margin_rate=Decimal("0.05"), unit_step=Decimal("0.1"), initial_paper_cash=Decimal("10000"),
        code_hash=CODE_HASH, qualification="unqualified",
    )
    values.update(changes)
    return ExperimentalRoundManifest(**values)


def test_round_is_immutable_and_account_bound(tmp_path):
    original = manifest()
    path = register_experimental_round(tmp_path, original, at=OPEN - timedelta(hours=1))
    assert path == tmp_path / "ExperimentalRounds" / original.round_id / "manifest.json"
    original_bytes = path.read_bytes()
    assert register_experimental_round(tmp_path, original, at=OPEN - timedelta(minutes=1)) == path
    assert path.read_bytes() == original_bytes
    with pytest.raises(ValueError):
        register_experimental_round(tmp_path, manifest(direction="short"), at=OPEN - timedelta(minutes=1))
    with pytest.raises(ValueError):
        load_experimental_round(path, account_feed_hash="e" * 64, code_hash=CODE_HASH, at=OPEN)


def test_missing_cost_source_or_wrong_product_cannot_register(tmp_path):
    with pytest.raises(ValueError):
        register_experimental_round(tmp_path, manifest(cost_source=""), at=OPEN - timedelta(hours=1))
    wrong = InstrumentSpec(provider="oanda_practice", broker_symbol="SPX500_USD", market="germany40", product="cfd", quote_currency="EUR", point_value=Decimal(1))
    with pytest.raises(ValueError):
        register_experimental_round(tmp_path, manifest(instrument=wrong), at=OPEN - timedelta(hours=1))
    with pytest.raises(ValueError):
        register_experimental_round(tmp_path, manifest(), at=OPEN)


def test_wrong_helper_hash_cannot_load(tmp_path):
    path = register_experimental_round(tmp_path, manifest(), at=OPEN - timedelta(hours=1))
    with pytest.raises(ValueError):
        load_experimental_round(path, account_feed_hash=ACCOUNT_HASH, code_hash="f" * 64, at=OPEN)


def test_manifest_contains_no_token(tmp_path):
    path = register_experimental_round(tmp_path, manifest(), at=OPEN - timedelta(hours=1))
    raw = path.read_text()
    assert "practice-001" not in raw
    assert "secret-token" not in raw
    with pytest.raises(ValueError):
        manifest(token="secret-token")
