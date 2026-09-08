import asyncio
import json
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


def test_fixed_end_metrics_do_not_change_with_post_end_quotes_or_gaps(tmp_path):
    end = 90 * 86400
    path = tmp_path / "ledger.db"
    with ProspectiveLedger(path, manifest()) as ledger:
        observe(ledger, quote(end - 61))
        observe(ledger, quote(end - 31))
        observe(ledger, quote(end - 1))
        at_end = row(ledger, end)
        assert at_end["coverage"]["observed_minutes"] == 1
    with ProspectiveLedger(path, manifest()) as ledger:
        observe(ledger, quote(end + 31, bid="200", ask="200.05"))
        later = row(ledger, end + 31)
        for key in ("coverage", "buy_and_hold_return", "cash", "equity", "net_pnl", "maximum_drawdown"):
            assert later[key] == at_end[key]


def test_late_liquidation_reported_separately_from_frozen_end_account(tmp_path):
    end = 90 * 86400
    path = tmp_path / "ledger.db"
    with ProspectiveLedger(path, manifest()) as ledger:
        observe(ledger, quote(end - 11))
        decision = START + timedelta(seconds=end - 10)
        ledger.record_signal(
            "c" * 64, decision_at=decision, bar_end=decision, reference_price=D("100"), atr=D("2"), signal_id="late"
        )
        observe(ledger, quote(end - 9))
        frozen = row(ledger, end)
        observe(ledger, quote(end + 1, bid="104", ask="104.05"))
        later = row(ledger, end + 1)
        assert later["cash"] == frozen["cash"]
        assert later["net_pnl"] == frozen["net_pnl"]
        assert later["position_quantity"] == frozen["position_quantity"]
        assert later["closed_trades"] == 0
        assert later["post_end_liquidation"]["closed_trades"] == 1
        assert later["post_end_liquidation"]["position_quantity"] == "0"
        assert "liquidation_after_fixed_end" in later["reasons"]


def test_closed_trade_history_is_not_rewritten_in_per_quote_state(tmp_path):
    path = tmp_path / "ledger.db"
    with ProspectiveLedger(path, manifest()) as ledger:
        for i in range(100):
            at = START + timedelta(seconds=3 * i)
            ledger.record_signal(
                "c" * 64, decision_at=at, bar_end=at, reference_price=D("100"), atr=D("2"), signal_id=f"trade-{i}"
            )
            observe(ledger, quote(3 * i + 1))
            observe(ledger, quote(3 * i + 2, bid="104", ask="104.05"))
        assert row(ledger, 300)["closed_trades"] == 100
    with sqlite3.connect(path) as conn:
        state_bytes = conn.execute("SELECT length(payload) FROM state").fetchone()[0]
        assert state_bytes < 10000
        assert conn.execute("SELECT COUNT(*) FROM journal WHERE kind='closed_trade'").fetchone()[0] == 100
    with ProspectiveLedger(path, manifest()) as ledger:
        assert row(ledger, 301)["closed_trades"] == 100
        assert ledger.verify_integrity()


def test_source_retransmission_cannot_consume_displayed_exit_size_twice(tmp_path):
    path = tmp_path / "ledger.db"
    stop_quote = quote(2, bid="97", ask="97.05", size="5")
    with ProspectiveLedger(path, manifest()) as ledger:
        signal(ledger)
        observe(ledger, quote())
        observe(ledger, stop_quote)
        assert row(ledger)["position_quantity"] == "6.1"
    with ProspectiveLedger(path, manifest()) as ledger:
        received = START + timedelta(seconds=2.5)
        replay = stop_quote.model_copy(update={"received_at": received, "processed_at": received})
        observe(ledger, replay)
        result = row(ledger, 2.5)
        assert result["position_quantity"] == "6.1"
        assert result["fills"] == 2
        # Advance another observation then retransmit the earlier economic quote again.
        observe(ledger, quote(3, bid="97", ask="97.05", size="0.01"))
        received = START + timedelta(seconds=3.5)
        replay = stop_quote.model_copy(update={"received_at": received, "processed_at": received})
        observe(ledger, replay)
        assert row(ledger, 3.5)["position_quantity"] == "6.1"


def test_same_provider_observation_with_changed_price_fails_closed(tmp_path):
    path = tmp_path / "ledger.db"
    with ProspectiveLedger(path, manifest()) as ledger:
        observe(ledger, quote())
    with ProspectiveLedger(path, manifest()) as ledger:
        received = START + timedelta(seconds=1.5)
        changed = quote(1, bid="99", ask="99.05").model_copy(update={"received_at": received, "processed_at": received})
        with pytest.raises(ValueError, match="conflicting"):
            observe(ledger, changed)


def test_first_post_end_fill_freezes_without_an_end_summary(tmp_path):
    end = 90 * 86400
    with ProspectiveLedger(tmp_path / "ledger.db", manifest()) as ledger:
        observe(ledger, quote(end - 11))
        decision = START + timedelta(seconds=end - 10)
        ledger.record_signal(
            "c" * 64, decision_at=decision, bar_end=decision, reference_price=D("100"), atr=D("2"), signal_id="last"
        )
        observe(ledger, quote(end - 9))
        observe(ledger, quote(end + 1, bid="104", ask="104.05"))
        result = row(ledger, end + 1)
        assert result["cash"] == "8887.7786122225"
        assert result["closed_trades"] == 0
        assert result["post_end_liquidation"]["closed_trades"] == 1


def test_returned_summary_cannot_modify_fixed_evidence(tmp_path):
    with ProspectiveLedger(tmp_path / "ledger.db", manifest()) as ledger:
        summary = row(ledger, 90 * 86400)
        summary["coverage"]["fraction"] = 1
        assert row(ledger, 90 * 86400)["coverage"]["fraction"] == 0


def test_provider_lead_requires_settled_processing_and_preserves_receipt(tmp_path):
    from src.live_monitor.timing import CausalClock, prepare_market_event

    received = START + timedelta(seconds=0.3)
    provider = START + timedelta(seconds=0.45)
    raw = quote(0.45).model_copy(update={"received_at": received, "processed_at": received})
    clock_time = received
    clock = CausalClock(lambda: clock_time)

    async def pause(seconds):
        nonlocal clock_time
        clock_time += timedelta(seconds=seconds)

    path = tmp_path / "ledger.db"
    with ProspectiveLedger(path, manifest()) as ledger:
        signal(ledger)
        ledger.on_quote(raw, now=received, lot_step=D(".1"), min_notional=D("10"))
        assert row(ledger, 0.3)["fills"] == 0
        settled = asyncio.run(prepare_market_event(raw, clock=clock.now, pause=pause))
        assert settled.received_at == received
        assert settled.processed_at == provider
        ledger.on_quote(settled, now=clock.now(), lot_step=D(".1"), min_notional=D("10"))
        assert row(ledger, 0.45)["fills"] == 1
    with sqlite3.connect(path) as conn:
        payload = json.loads(conn.execute("SELECT payload FROM journal WHERE kind='fills'").fetchone()[0])
        assert payload["quote"]["received_at"] == received.isoformat().replace("+00:00", "Z")


def test_future_provider_time_cannot_bypass_receipt_entry_latency(tmp_path):
    from src.live_monitor.timing import CausalClock, prepare_market_event

    received = START + timedelta(seconds=0.1)
    clock_time = received
    clock = CausalClock(lambda: clock_time)
    raw = quote(0.3).model_copy(update={"received_at": received, "processed_at": received})

    async def pause(seconds):
        nonlocal clock_time
        clock_time += timedelta(seconds=seconds)

    settled = asyncio.run(prepare_market_event(raw, clock=clock.now, pause=pause))
    with ProspectiveLedger(tmp_path / "ledger.db", manifest()) as ledger:
        signal(ledger)
        ledger.on_quote(settled, now=clock.now(), lot_step=D(".1"), min_notional=D("10"))
        assert row(ledger, 0.3)["fills"] == 0
        assert ledger.summary(now=clock.now())["evidence_counts"]["quotes"] == 1
        replay = settled.model_copy(
            update={"received_at": START + timedelta(seconds=0.5), "processed_at": START + timedelta(seconds=0.5)}
        )
        observe(ledger, replay)
        assert row(ledger, 0.5)["fills"] == 0
        observe(ledger, quote(0.6))
        assert row(ledger, 0.6)["fills"] == 1


def test_quote_provider_lead_over_one_second_fails_closed(tmp_path):
    # Even model_copy input cannot bypass the ledger's frozen provenance bound.
    raw = quote(2).model_copy(update={"received_at": START, "processed_at": START + timedelta(seconds=2)})
    with ProspectiveLedger(tmp_path / "ledger.db", manifest()) as ledger:
        signal(ledger)
        ledger.on_quote(raw, now=START + timedelta(seconds=2), lot_step=D(".1"), min_notional=D("10"))
        assert ledger.summary(now=START + timedelta(seconds=2))["evidence_counts"]["quotes"] == 0
