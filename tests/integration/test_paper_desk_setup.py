"""Production setup, not a test-only strategy registry."""

from datetime import UTC, datetime

import pandas as pd
import pytest

from src.research.paper_desk_setup import initialize_paper_desk
from src.research.round_two_runtime import _strategy_registry
from src.research.round_two_walkforward import _parameter_specs
from src.strategies.library import StrategyContext, audit_prefix_invariance


def test_new_setup_resolves_real_rules_and_does_not_repaint(tmp_path):
    protocol = initialize_paper_desk(tmp_path / "desk", now=datetime(2026, 9, 27, tzinfo=UTC))
    assert set(protocol.symbols) == {"BTCUSDT", "ETHUSDT"}
    assert len(protocol.candidates) == 6
    registry = _strategy_registry()
    times = pd.date_range("2026-09-27", periods=160, freq="min", tz="UTC")
    close = list(range(100, 200)) + list(range(200, 140, -1))
    bars = pd.DataFrame(
        dict(
            provider="binance",
            feed="spot",
            symbol="BTCUSDT",
            interval="1m",
            open_timestamp=times,
            close_timestamp=times + pd.Timedelta(minutes=1),
            available_at=times + pd.Timedelta(minutes=1),
            finalized=True,
            revision=1,
            open=close,
            high=[n + 0.25 for n in close],
            low=[n - 0.25 for n in close],
            close=close,
            volume=1000,
        )
    )
    context = StrategyContext.for_market("binance", "spot")
    for candidate in protocol.candidates:
        item = registry.resolve(candidate.strategy_id)
        (spec,) = _parameter_specs(candidate, item)
        signals = item.generator(spec, bars, context)
        assert signals.iloc[95]["signal"] == 1
        assert signals.iloc[-1]["signal"] == -1
        assert audit_prefix_invariance(spec, bars.iloc[:100], bars, context, context, generator=item.generator).passed


def test_resume_preserves_protocol_and_losses(tmp_path):
    directory = tmp_path / "desk"
    first = initialize_paper_desk(directory, now=datetime(2026, 9, 27, tzinfo=UTC))
    before = (directory / "protocol.json").read_bytes()
    ledger = directory / "losses.jsonl"
    ledger.write_text('{"retained_loss":100}\n')
    second = initialize_paper_desk(directory, now=datetime(2026, 10, 1, tzinfo=UTC))
    assert first.identity_hash == second.identity_hash
    assert (directory / "protocol.json").read_bytes() == before
    assert ledger.read_text() == '{"retained_loss":100}\n'


def test_setup_refuses_unregistered_nonempty_directory(tmp_path):
    (tmp_path / "existing.txt").write_text("keep")
    with pytest.raises(ValueError, match="empty"):
        initialize_paper_desk(tmp_path)
    assert not (tmp_path / "protocol.json").exists()
    assert (tmp_path / "existing.txt").read_text() == "keep"


def test_setup_refuses_protected_symlink(tmp_path):
    protected = tmp_path / "ProspectiveStudies" / "retained"
    protected.mkdir(parents=True)
    alias = tmp_path / "alias"
    alias.symlink_to(protected, target_is_directory=True)
    with pytest.raises(ValueError, match="protected"):
        initialize_paper_desk(alias)
    assert not list(protected.iterdir())
