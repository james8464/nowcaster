import inspect
import json
from datetime import UTC, datetime

import httpx
import pytest

import scripts.intraday_service_entry as entry
from scripts.intraday_service_entry import RetainedReportCache, run_paper_indicator
from src.intraday.oanda_practice import PRACTICE_API, PRACTICE_STREAM, OandaPracticeFeed


class FakeFeed:
    def available_instruments(self):
        return ({"name": "DE30_EUR", "type": "CFD", "displayName": "Germany 30", "marginRate": "0.05"},)

    def price_lines(self, instruments):
        assert [item.broker_symbol for item in instruments] == ["DE30_EUR"]
        yield json.dumps(
            {
                "type": "PRICE",
                "instrument": "DE30_EUR",
                "time": "2026-10-08T09:00:00Z",
                "tradeable": True,
                "bids": [{"price": "24000"}],
                "asks": [{"price": "24002"}],
            }
        )


def test_practice_http_stream_failure_retries_without_exposing_account(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OANDA_PRACTICE_ACCOUNT_ID", "private-account")
    monkeypatch.setenv("OANDA_PRACTICE_TOKEN", "private-token")
    attempts = []

    def interrupted(directory, **_kwargs):
        attempts.append(directory)
        if len(attempts) == 1:
            request = httpx.Request("GET", "https://stream-fxpractice.oanda.com/v3/accounts/private-account/pricing/stream")
            httpx.Response(520, request=request).raise_for_status()
        (directory / "pause.request").write_text("pause\n")

    monkeypatch.setattr(entry, "run_paper_indicator", interrupted)
    monkeypatch.setattr(entry.time, "sleep", lambda _seconds: None)
    assert entry.main(["run", "--directory", str(tmp_path)]) == 0
    assert attempts == [tmp_path, tmp_path]
    stderr = capsys.readouterr().err
    assert "Practice data interrupted" in stderr
    assert "private-account" not in stderr
    assert "private-token" not in stderr


def test_practice_runner_keeps_token_and_account_out_of_files(tmp_path):
    run_paper_indicator(
        tmp_path,
        account_id="private-account",
        token="private-token",
        feed=FakeFeed(),
        now=lambda: datetime(2026, 10, 8, 9, tzinfo=UTC),
    )
    text = "\n".join(path.read_text() for path in tmp_path.rglob("*") if path.is_file())
    assert "private-token" not in text
    assert "private-account" not in text
    assert "Germany 30" in text
    assert "no_trade_reason" in text
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["markets"][0]["display_name"] == "Germany 30"


def test_practice_runner_publishes_retained_account_capture_quality(tmp_path):
    from datetime import timedelta

    start = datetime(2026, 10, 8, 9, tzinfo=UTC)
    clock = [start]

    class QualityFeed(FakeFeed):
        def price_lines(self, instruments):
            for minute in (1, 6):
                clock[0] = start + timedelta(minutes=minute)
                yield json.dumps({
                    "type": "PRICE", "instrument": "DE30_EUR", "time": clock[0].isoformat(),
                    "tradeable": True, "bids": [{"price": "24000"}], "asks": [{"price": "24002"}],
                })

    run_paper_indicator(tmp_path, account_id="private-account", token="private-token", feed=QualityFeed(), now=lambda: clock[0])
    quality = json.loads((tmp_path / "capture_quality.json").read_text())
    assert quality["price_scope"] == "account_stream_observation"
    assert quality["markets"][0]["broker_symbol"] == "DE30_EUR"
    assert quality["markets"][0]["expected_intervals"] == 25
    assert quality["markets"][0]["covered_intervals"] == 1
    assert quality["markets"][0]["paper_eligible"] is False
    assert (tmp_path / "2026-10-08" / "capture_quality.json").exists()


def test_practice_boundary_has_no_order_route_or_live_host():
    source = inspect.getsource(OandaPracticeFeed)
    assert PRACTICE_API == "https://api-fxpractice.oanda.com"
    assert PRACTICE_STREAM == "https://stream-fxpractice.oanda.com"
    assert "/orders" not in source
    assert "api-fxtrade.oanda.com" not in source


def test_practice_report_refreshes_during_same_session(tmp_path):
    class TwoQuoteFeed(FakeFeed):
        def price_lines(self, instruments):
            yield from super().price_lines(instruments)
            yield json.dumps(
                {
                    "type": "PRICE",
                    "instrument": "DE30_EUR",
                    "time": "2026-10-08T09:02:00Z",
                    "tradeable": True,
                    "bids": [{"price": "24001"}],
                    "asks": [{"price": "24003"}],
                }
            )

    times = iter(
        (
            datetime(2026, 10, 8, 9, tzinfo=UTC),
            datetime(2026, 10, 8, 9, tzinfo=UTC),
            datetime(2026, 10, 8, 9, tzinfo=UTC),
            datetime(2026, 10, 8, 9, 2, tzinfo=UTC),
        )
    )
    run_paper_indicator(
        tmp_path, account_id="private-account", token="private-token", feed=TwoQuoteFeed(), now=lambda: next(times)
    )
    report = json.loads((tmp_path / "report.json").read_text())
    assert datetime.fromisoformat(report["generated_at"]) >= datetime(2026, 10, 8, 9, 2, tzinfo=UTC)


def test_practice_monitor_can_start_before_declared_market_session(tmp_path):
    class EarlyFeed(FakeFeed):
        def price_lines(self, instruments):
            yield json.dumps(
                {
                    "type": "PRICE",
                    "instrument": "DE30_EUR",
                    "time": "2026-10-08T06:00:00Z",
                    "tradeable": False,
                    "bids": [{"price": "24000"}],
                    "asks": [{"price": "24002"}],
                }
            )

    run_paper_indicator(
        tmp_path,
        account_id="private-account",
        token="private-token",
        feed=EarlyFeed(),
        now=lambda: datetime(2026, 10, 8, 6, tzinfo=UTC),
    )
    assert json.loads((tmp_path / "report.json").read_text())["closed_trades"] == 0


def test_practice_report_retains_prior_day_decisions_after_restart(tmp_path):
    class DatedFeed(FakeFeed):
        def __init__(self, timestamp):
            self.timestamp = timestamp

        def price_lines(self, instruments):
            yield json.dumps(
                {
                    "type": "PRICE",
                    "instrument": "DE30_EUR",
                    "time": self.timestamp.isoformat().replace("+00:00", "Z"),
                    "tradeable": True,
                    "bids": [{"price": "24000"}],
                    "asks": [{"price": "24002"}],
                }
            )

    from src.intraday.journal import PaperJournal
    from src.intraday.live_service import LiveRoundManifest

    first = datetime(2026, 10, 8, 9, tzinfo=UTC)
    second = datetime(2026, 10, 9, 9, tzinfo=UTC)
    run_paper_indicator(
        tmp_path, account_id="private-account", token="private-token", feed=DatedFeed(first), now=lambda: first
    )
    manifest = LiveRoundManifest.model_validate_json((tmp_path / "2026-10-08" / "live_round.json").read_text())
    paper = PaperJournal(tmp_path / "PaperRounds" / "2026-10-08", manifest.identity_hash)
    with paper as writer:
        writer.append("no_trade", first, {"broker_symbol": "DE30_EUR", "reason": "costs_unverified"})
    run_paper_indicator(
        tmp_path, account_id="private-account", token="private-token", feed=DatedFeed(second), now=lambda: second
    )
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["round_id"] == "all-retained-practice-rounds"
    assert report["no_trade_count"] >= 1
    assert report["blocked_reasons"]["costs_unverified"] == 1


def test_ready_diagnostic_setup_records_explicit_paper_rejection(tmp_path, monkeypatch):
    from decimal import Decimal

    from src.intraday.strategies import SetupDecision

    clock = [datetime(2026, 10, 8, 9, tzinfo=UTC)]

    class BarFeed(FakeFeed):
        def price_lines(self, instruments):
            from datetime import timedelta

            for seconds in (*range(0, 300, 10), 300, 301):
                clock[0] = datetime(2026, 10, 8, 9, tzinfo=UTC) + timedelta(seconds=seconds)
                yield json.dumps(
                    {
                        "type": "PRICE",
                        "instrument": "DE30_EUR",
                        "time": clock[0].isoformat().replace("+00:00", "Z"),
                        "tradeable": True,
                        "bids": [{"price": "24000"}],
                        "asks": [{"price": "24002"}],
                    }
                )

    def ready(_rule, _bars, quote, **_kwargs):
        from datetime import timedelta

        return SetupDecision(
            status="ready",
            strategy_id="trend_pullback",
            direction="long",
            reason="test setup",
            decision_at=quote.observed_at - timedelta(seconds=1),
            entry_at=quote.received_at,
            entry=Decimal("24002"),
            stop=Decimal("23990"),
            target=Decimal("24026"),
            exit_by=quote.received_at + timedelta(minutes=10),
            estimated_roundtrip_cost=Decimal("2"),
            evidence_hash="a" * 64,
        )

    monkeypatch.setattr("src.intraday.live_service.evaluate_setup", ready)
    run_paper_indicator(
        tmp_path, account_id="private-account", token="private-token", feed=BarFeed(), now=lambda: clock[0]
    )
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["decisions_count"] == 1
    assert report["no_trade_count"] == 1
    assert report["blocked_reasons"] == {"selection_and_cost_evidence_missing": 1}
    assert report["closed_trades"] == 0
    assert not (tmp_path / "PaperRounds" / "2026-10-08" / "events.jsonl").exists()


def test_same_day_restart_uses_report_clock_for_retained_gap(tmp_path):
    at = datetime(2026, 10, 8, 6, tzinfo=UTC)

    class MorningFeed(FakeFeed):
        def price_lines(self, instruments):
            yield json.dumps(
                {
                    "type": "PRICE",
                    "instrument": "DE30_EUR",
                    "time": at.isoformat().replace("+00:00", "Z"),
                    "tradeable": True,
                    "bids": [{"price": "24000"}],
                    "asks": [{"price": "24002"}],
                }
            )

    for _ in range(2):
        run_paper_indicator(
            tmp_path, account_id="private-account", token="private-token", feed=MorningFeed(), now=lambda: at
        )
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["feed_gap_count"] >= 1


def test_retained_report_rejects_changed_account_and_caches_closed_day(tmp_path, monkeypatch):
    import hashlib
    from datetime import timedelta

    first = datetime(2026, 10, 8, 9, tzinfo=UTC)
    second = first + timedelta(days=1)

    class TimedFeed(FakeFeed):
        def __init__(self, at):
            self.at = at

        def price_lines(self, instruments):
            yield json.dumps(
                {
                    "type": "PRICE",
                    "instrument": "DE30_EUR",
                    "time": self.at.isoformat().replace("+00:00", "Z"),
                    "tradeable": True,
                    "bids": [{"price": "24000"}],
                    "asks": [{"price": "24002"}],
                }
            )

    run_paper_indicator(
        tmp_path, account_id="account-A", token="private-token", feed=TimedFeed(first), now=lambda: first
    )
    run_paper_indicator(
        tmp_path, account_id="account-A", token="private-token", feed=TimedFeed(second), now=lambda: second
    )
    cache = RetainedReportCache(tmp_path, hashlib.sha256(b"account-A").hexdigest())
    cache.snapshot(second)
    original = cache._read_day
    read_days = []

    def tracked(day, as_of, **kwargs):
        read_days.append(day)
        return original(day, as_of, **kwargs)

    monkeypatch.setattr(cache, "_read_day", tracked)
    cache.snapshot(second + timedelta(minutes=1))
    assert read_days == ["2026-10-09"]
    with pytest.raises(ValueError, match="account changed"):
        run_paper_indicator(
            tmp_path,
            account_id="account-B",
            token="private-token",
            feed=TimedFeed(second + timedelta(days=1)),
            now=lambda: second + timedelta(days=1),
        )


def test_slow_report_does_not_block_next_account_quote(tmp_path, monkeypatch):
    from datetime import timedelta
    from threading import Event

    first = datetime(2026, 10, 8, 9, tzinfo=UTC)
    clock = [first]
    started = Event()
    release = Event()
    advanced = Event()
    original = RetainedReportCache.snapshot

    def slow_snapshot(self, as_of, *, ignore_after_as_of=False):
        if ignore_after_as_of:
            started.set()
            if not release.wait(2):
                raise AssertionError("quote processing blocked on report")
        return original(self, as_of, ignore_after_as_of=ignore_after_as_of)

    monkeypatch.setattr(RetainedReportCache, "snapshot", slow_snapshot)

    class TwoQuoteFeed(FakeFeed):
        def price_lines(self, instruments):
            clock[0] = first + timedelta(minutes=1)
            yield json.dumps(
                {
                    "type": "PRICE",
                    "instrument": "DE30_EUR",
                    "time": clock[0].isoformat().replace("+00:00", "Z"),
                    "tradeable": True,
                    "bids": [{"price": "24000"}],
                    "asks": [{"price": "24002"}],
                }
            )
            assert started.wait(2)
            advanced.set()
            release.set()
            clock[0] = first + timedelta(minutes=1, seconds=1)
            yield json.dumps(
                {
                    "type": "PRICE",
                    "instrument": "DE30_EUR",
                    "time": clock[0].isoformat().replace("+00:00", "Z"),
                    "tradeable": True,
                    "bids": [{"price": "24000"}],
                    "asks": [{"price": "24002"}],
                }
            )

    run_paper_indicator(
        tmp_path, account_id="private-account", token="private-token", feed=TwoQuoteFeed(), now=lambda: clock[0]
    )
    assert advanced.is_set()
