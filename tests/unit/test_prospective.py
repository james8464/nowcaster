import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from src.live_monitor.types import MarketQuote
from src.research.prospective import ProspectiveLedger
from src.research.prospective_types import StudyCandidate, StudyManifest

START = datetime(2026, 9, 9, tzinfo=UTC)


def manifest(**changes):
    values = dict(
        study_number=1,
        registered_at=START,
        starts_at=START,
        ends_at=START + timedelta(days=90),
        source_hash="a" * 64,
        discovery_hash="b" * 64,
        candidates=(
            StudyCandidate(
                candidate_id="c" * 64,
                symbol="BTCUSDT",
                strategy_id="macd_histogram_trend",
                strategy_definition_hash="d" * 64,
                stop_atr=1,
                target_atr=1.5,
                maximum_bars=6,
                screen_passed=False,
            ),
        ),
    )
    return StudyManifest(**(values | changes))


def quote(seconds=1, *, bid="100", ask="100.05", size="100", feed="spot", sequence=None):
    at = START + timedelta(seconds=seconds)
    return MarketQuote(
        provider="binance",
        feed=feed,
        symbol="BTCUSDT",
        bid=D(bid),
        ask=D(ask),
        bid_size=D(size),
        ask_size=D(size),
        last=D(bid),
        tick_size=D("0.01"),
        provider_time=at,
        received_at=at,
        sequence=sequence,
    )


def signal(ledger, *, signal_id="signal-1", seconds=0, reference="100"):
    at = START + timedelta(seconds=seconds)
    ledger.record_signal(
        "c" * 64, decision_at=at, bar_end=START, reference_price=D(reference), atr=D("2"), signal_id=signal_id
    )


def observe(ledger, q, **kwargs):
    ledger.on_quote(q, now=q.received_at, lot_step=D("0.1"), min_notional=D("10"), **kwargs)


def row(ledger, seconds=2):
    return ledger.summary(now=START + timedelta(seconds=seconds))["candidates"][0]


def test_restart_exactly_once_and_hand_calculated_account(tmp_path):
    path = tmp_path / "ledger.db"
    m = manifest()
    with ProspectiveLedger(path, m) as ledger:
        signal(ledger)
        observe(ledger, quote())
        first = row(ledger)
        # Entry-relative stop 98.100025; risk including costs is 2.2472010124875/unit.
        assert D(first["position_quantity"]) == D("11.1")
        # 11.1 * 100.05 * 1.0005 * 1.001 = 1112.2213877775
        assert D(first["cash"]) == D("8887.7786122225")
    with ProspectiveLedger(path, m) as ledger:
        signal(ledger)
        observe(ledger, quote())
        assert row(ledger)["fills"] == 1
        observe(ledger, quote(3, bid="104", ask="104.05"))
        final = row(ledger, 3)
        # Sale proceeds: 11.1 * 104 * .9995 * .999 = 1152.6689772.
        assert D(final["cash"]) == D("10040.4475894225")
        assert final["closed_trades"] == 1
        assert final["fills"] == 2
        assert ledger.verify_integrity()


def test_conflicting_signals_manifest_writer_and_corruption(tmp_path):
    path = tmp_path / "ledger.db"
    with ProspectiveLedger(path, manifest()) as ledger:
        signal(ledger)
        with pytest.raises(ValueError, match="conflict"):
            signal(ledger, reference="101")
        with pytest.raises(RuntimeError, match="writer"):
            ProspectiveLedger(path, manifest())
    with pytest.raises(ValueError, match="manifest"):
        ProspectiveLedger(path, manifest(source_hash="e" * 64))
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE journal SET payload='{}' WHERE seq=1")
    with pytest.raises(ValueError, match="integrity"):
        ProspectiveLedger(path, manifest())


@pytest.mark.parametrize(
    "seconds,size,feed", [(0.1, "100", "spot"), (1, "0.01", "spot"), (1, "100", "seed"), (31, "100", "spot")]
)
def test_no_early_undersized_seed_or_expired_entry(tmp_path, seconds, size, feed):
    with ProspectiveLedger(tmp_path / "ledger.db", manifest()) as ledger:
        signal(ledger)
        observe(ledger, quote(seconds, size=size, feed=feed))
        assert row(ledger, max(seconds, 2))["fills"] == 0


def test_stale_quote_and_backward_clock_rejected(tmp_path):
    with ProspectiveLedger(tmp_path / "ledger.db", manifest()) as ledger:
        signal(ledger)
        ledger.on_quote(quote(), now=START + timedelta(seconds=4), lot_step=D("0.1"), min_notional=D("10"))
        assert row(ledger, 4)["fills"] == 0
        with pytest.raises(ValueError, match="backward"):
            observe(ledger, quote(2))


def test_stop_latches_partial_exit_gap_taints_loss(tmp_path):
    with ProspectiveLedger(tmp_path / "ledger.db", manifest()) as ledger:
        signal(ledger)
        observe(ledger, quote())
        observe(ledger, quote(2, bid="97", ask="97.05", size="5"))
        assert D(row(ledger)["position_quantity"]) == D("6.1")
        ledger.record_gap(at=START + timedelta(seconds=40), reason="offline")
        observe(ledger, quote(41, bid="100", ask="100.05"))
        result = row(ledger, 41)
        assert result["closed_trades"] == 1
        assert result["tainted_trades"] == 1
        assert D(result["net_pnl"]) < 0
        assert result["position_quantity"] == "0"


def test_window_and_metadata_and_no_early_success(tmp_path):
    with ProspectiveLedger(tmp_path / "ledger.db", manifest()) as ledger:
        with pytest.raises(ValueError):
            signal(ledger, seconds=-1)
        signal(ledger)
        with pytest.raises(ValueError):
            ledger.on_quote(quote(), now=quote().received_at, lot_step=D("0"), min_notional=D("10"))
        result = ledger.summary(now=START + timedelta(days=1))
        assert result["status"] == "collecting"
        assert result["paper_only"] is True
        assert result["candidates"][0]["cash"] == "10000"
        result = ledger.summary(now=START + timedelta(days=90))
        assert result["status"] == "insufficient_evidence"


def test_manifest_rejects_mutable_or_changed_window():
    with pytest.raises(ValueError):
        manifest(ends_at=START + timedelta(days=89))
    with pytest.raises(ValueError):
        manifest(registered_at=START + timedelta(seconds=1))
    with pytest.raises(ValueError):
        manifest().candidates[0].screen_passed = True


def test_entry_caps_exposure_against_remaining_equity_after_loss(tmp_path):
    with ProspectiveLedger(tmp_path / "ledger.db", manifest()) as ledger:
        signal(ledger)
        observe(ledger, quote())
        observe(ledger, quote(2, bid="1", ask="1.0005"))
        cash = D(row(ledger)["cash"])
        # Finalized new signal at 3s, tighter ATR makes exposure the limiting constraint.
        ledger.record_signal(
            "c" * 64,
            decision_at=START + timedelta(seconds=3),
            bar_end=START,
            reference_price=D("1"),
            atr=D(".005"),
            signal_id="second",
        )
        observe(ledger, quote(4, bid="1", ask="1.0005", size="10000"))
        result = row(ledger, 4)
        spent = cash - D(result["cash"])
        assert D("0") < spent <= cash * D(".25")


def test_expiry_latches_even_without_sufficient_displayed_exit_size(tmp_path):
    with ProspectiveLedger(tmp_path / "ledger.db", manifest()) as ledger:
        signal(ledger)
        observe(ledger, quote())
        observe(ledger, quote(21601, size="0.01"))
        assert row(ledger, 21601)["closed_trades"] == 0
        observe(ledger, quote(21602))
        assert row(ledger, 21602)["closed_trades"] == 1
        assert row(ledger, 21602)["tainted_trades"] == 1


def test_state_corruption_detected_and_original_frozen_decision_retained(tmp_path):
    path = tmp_path / "ledger.db"
    with ProspectiveLedger(path, manifest()) as ledger:
        signal(ledger)
        observe(ledger, quote(31))
        assert row(ledger, 31)["decisions"] == 1
        assert not row(ledger, 31)["pending_entry"]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM journal WHERE kind='decision'").fetchone()[0] == 1
        conn.execute("UPDATE state SET payload='{}'")
    with pytest.raises(ValueError, match="integrity"):
        ProspectiveLedger(path, manifest())


def test_conflicting_quote_sequence_rejected(tmp_path):
    with ProspectiveLedger(tmp_path / "ledger.db", manifest()) as ledger:
        observe(ledger, quote(sequence=1))
        with pytest.raises(ValueError, match="conflicting"):
            observe(ledger, quote(2, sequence=1, bid="99", ask="99.05"))


def test_deleting_journal_tail_fails_closed(tmp_path):
    path = tmp_path / "ledger.db"
    with ProspectiveLedger(path, manifest()) as ledger:
        signal(ledger)
        observe(ledger, quote())
    with sqlite3.connect(path) as conn:
        conn.execute("DELETE FROM journal WHERE seq=(SELECT MAX(seq) FROM journal)")
    with pytest.raises(ValueError, match="integrity"):
        ProspectiveLedger(path, manifest())


def test_complete_utc_day_includes_zero_return_observations(tmp_path):
    with ProspectiveLedger(tmp_path / "ledger.db", manifest()) as ledger:
        # Public observations every 30 seconds through the next midnight.
        for seconds in range(0, 86401, 30):
            observe(ledger, quote(seconds))
        coverage = row(ledger, 86400)["coverage"]
        assert coverage["observed_minutes"] == 1440
        assert coverage["fraction"] == 1
        assert coverage["complete_daily_returns"] == [0.0]
        assert coverage["all_full_utc_days_complete"]


def test_incomplete_day_is_not_silently_zero_filled(tmp_path):
    with ProspectiveLedger(tmp_path / "ledger.db", manifest()) as ledger:
        observe(ledger, quote(0))
        observe(ledger, quote(86400))
        coverage = row(ledger, 86400)["coverage"]
        assert coverage["complete_daily_returns"] == []
        assert not coverage["all_full_utc_days_complete"]
        assert coverage["fraction"] == 0
