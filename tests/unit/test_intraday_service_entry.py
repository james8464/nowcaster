import inspect
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

import scripts.intraday_service_entry as entry
from scripts.intraday_service_entry import RetainedReportCache, run_paper_indicator
from src.intraday.live_service import LiveIndicatorSession
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
            request = httpx.Request(
                "GET", "https://stream-fxpractice.oanda.com/v3/accounts/private-account/pricing/stream"
            )
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


def test_shutdown_request_ends_runner_without_restarting(tmp_path, monkeypatch):
    monkeypatch.setenv("OANDA_PRACTICE_ACCOUNT_ID", "private-account")
    monkeypatch.setenv("OANDA_PRACTICE_TOKEN", "private-token")
    attempts = []

    def stopped(directory, **_kwargs):
        attempts.append(directory)
        if len(attempts) > 1:
            raise AssertionError("shutdown restarted the collector")
        (directory / "shutdown.request").write_text("shutdown\n")

    monkeypatch.setattr(entry, "run_paper_indicator", stopped)
    monkeypatch.setattr(entry.time, "sleep", lambda _seconds: None)
    assert entry.main(["run", "--directory", str(tmp_path)]) == 0
    assert attempts == [tmp_path]


@pytest.mark.parametrize("control", ["pause", "shutdown"])
def test_control_request_precedes_next_quote_dispatch(tmp_path, monkeypatch, control):
    dispatched = []
    original = LiveIndicatorSession.on_event

    def observed_event(self, line, received):
        dispatched.append(line)
        return original(self, line, received)

    monkeypatch.setattr(LiveIndicatorSession, "on_event", observed_event)

    class RequestedFeed(FakeFeed):
        def price_lines(self, instruments):
            (tmp_path / f"{control}.request").write_text(control)
            yield from super().price_lines(instruments)

    run_paper_indicator(
        tmp_path, account_id="private-account", token="private-token", feed=RequestedFeed(),
        now=lambda: datetime(2026, 10, 8, 9, tzinfo=UTC),
    )
    assert dispatched == []


@pytest.mark.parametrize(
    "failure",
    [ValueError("quote journal chain mismatch"), RuntimeError("retained report changed"), OSError("disk full")],
)
def test_practice_integrity_failure_stops_instead_of_reconnecting(tmp_path, monkeypatch, capsys, failure):
    monkeypatch.setenv("OANDA_PRACTICE_ACCOUNT_ID", "private-account")
    monkeypatch.setenv("OANDA_PRACTICE_TOKEN", "private-token")
    attempts = []

    def broken(directory, **_kwargs):
        attempts.append(directory)
        if len(attempts) > 1:
            (directory / "pause.request").write_text("pause\n")
        raise failure

    monkeypatch.setattr(entry, "run_paper_indicator", broken)
    monkeypatch.setattr(entry.time, "sleep", lambda _seconds: None)
    assert entry.main(["run", "--directory", str(tmp_path)]) == 2
    assert attempts == [tmp_path]
    stderr = capsys.readouterr().err
    assert "monitoring stopped" in stderr
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


def _register_fixture_experiment(tmp_path, monkeypatch):
    import hashlib
    from decimal import Decimal

    from src.intraday.contracts import InstrumentSpec
    from src.intraday.experimental_round import ExperimentalRoundManifest, register_experimental_round
    from src.intraday.paper import PaperExecutionCosts

    current = datetime(2026, 10, 8, 9, tzinfo=UTC)
    cost_source = "https://www.oanda.com/uk-en/trading/fees/"
    cost_evidence = json.dumps({
        "account_feed_hash": hashlib.sha256(b"private-account").hexdigest(),
        "broker_symbol": "DE30_EUR", "product": "cfd", "quote_currency": "EUR",
        "account_currency": "GBP", "margin_rate": "0.05", "unit_step": "0.01",
        "slippage_points": "0.5", "commission_per_unit": "0", "financing_per_unit": "0",
        "verified_at": (current - timedelta(days=1)).isoformat(),
        "source_url": cost_source,
        "source_excerpt": "DE30_EUR CFD account-specific fee record for this test fixture.",
    }, sort_keys=True).encode()
    product = InstrumentSpec(
        provider="oanda_practice",
        broker_symbol="DE30_EUR",
        market="germany40",
        product="cfd",
        quote_currency="EUR",
        point_value=Decimal(1),
    )
    round_manifest = ExperimentalRoundManifest(
        round_id="de30-2026-10-08-a",
        cohort_id="de30-or15-long-v1",
        account_feed_hash=hashlib.sha256(b"private-account").hexdigest(),
        instrument=product,
        strategy_id="opening_range_15",
        direction="long",
        session_open=current - timedelta(hours=2),
        session_close=current + timedelta(hours=11),
        calendar_hash="c" * 64,
        costs=PaperExecutionCosts(verified=True, slippage_points=Decimal("0.5")),
        cost_source=cost_source,
        cost_source_hash=hashlib.sha256(cost_evidence).hexdigest(),
        verified_at=current - timedelta(days=1),
        account_currency="GBP",
        margin_rate=Decimal("0.05"),
        unit_step=Decimal("0.01"),
        initial_paper_cash=Decimal("10000"),
        code_hash="b" * 64,
    )
    register_experimental_round(tmp_path, round_manifest, cost_evidence=cost_evidence, at=current - timedelta(hours=3))
    monkeypatch.setattr(entry, "_helper_code_hash", lambda: "b" * 64)
    return current, round_manifest


def test_registered_experiment_receives_same_price_without_changing_diagnostic_schema(tmp_path, monkeypatch):
    current, round_manifest = _register_fixture_experiment(tmp_path, monkeypatch)
    run_paper_indicator(
        tmp_path, account_id="private-account", token="private-token", feed=FakeFeed(), now=lambda: current
    )
    diagnostic = json.loads((tmp_path / "summary.json").read_text())
    experimental = json.loads((tmp_path / "experimental-summary.json").read_text())
    experimental_report = json.loads((tmp_path / "experimental-report.json").read_text())
    assert diagnostic["markets"][0]["eligibility"] == "diagnostic"
    assert "qualification" not in diagnostic
    assert experimental["qualification"] == "unqualified"
    assert experimental["broker_symbol"] == "DE30_EUR"
    assert experimental_report["qualification"] == "unqualified"
    assert experimental_report["performance"]["closed_trades"] == 0
    assert experimental_report["performance"]["net_expectancy_gbp"] is None
    retained_round = tmp_path / "ExperimentalRounds" / round_manifest.round_id
    assert json.loads((retained_round / "summary.json").read_text())["round_id"] == round_manifest.round_id
    assert json.loads((retained_round / "report.json").read_text())["round_id"] == round_manifest.round_id
    captured = (tmp_path / "ExperimentalRounds" / round_manifest.round_id / "capture" / "quotes.jsonl").read_text()
    assert '"kind": "quote"' in captured


@pytest.mark.parametrize("control", ["pause", "shutdown"])
def test_control_arriving_during_diagnostic_processing_blocks_experimental_entry(tmp_path, monkeypatch, control):
    current, round_manifest = _register_fixture_experiment(tmp_path, monkeypatch)
    original = LiveIndicatorSession.on_event

    def request_after_diagnostic(self, line, received):
        result = original(self, line, received)
        (tmp_path / f"{control}.request").write_text(control)
        return result

    monkeypatch.setattr(LiveIndicatorSession, "on_event", request_after_diagnostic)
    run_paper_indicator(
        tmp_path, account_id="private-account", token="private-token", feed=FakeFeed(), now=lambda: current,
    )
    captured = tmp_path / "ExperimentalRounds" / round_manifest.round_id / "capture" / "quotes.jsonl"
    assert not captured.exists() or '"kind": "quote"' not in captured.read_text()


def test_open_paper_position_survives_utc_day_rollover(tmp_path, monkeypatch):
    import hashlib
    from decimal import Decimal

    from src.intraday.contracts import MarketQuote
    from src.intraday.experimental_service import ExperimentalPaperService
    from src.intraday.paper import FXConversion

    current, round_manifest = _register_fixture_experiment(tmp_path, monkeypatch)
    round_dir = tmp_path / "ExperimentalRounds" / round_manifest.round_id
    service = ExperimentalPaperService(round_manifest, round_dir)
    for n, price in enumerate(("100", "100", "101", "104")):
        for seconds in range(n * 300 + 1, (n + 1) * 300, 10):
            at = round_manifest.session_open + timedelta(seconds=seconds)
            item = MarketQuote(
                instrument=round_manifest.instrument,
                account_feed_hash=hashlib.sha256(b"private-account").hexdigest(),
                observed_at=at, received_at=at + timedelta(milliseconds=100),
                bid=Decimal(price), ask=Decimal(price) + 2, status="tradeable",
                source_key=f"prime-{seconds}",
            )
            conversion = FXConversion(
                from_currency="EUR", to_currency="GBP", position_value=Decimal("0.85"),
                account_gain=Decimal("0.84"), account_loss=Decimal("0.86"), observed_at=item.received_at,
            )
            service.on_quote(item, conversion=conversion)
    for seconds in (1201, 1202, 1203):
        at = round_manifest.session_open + timedelta(seconds=seconds)
        item = MarketQuote(
            instrument=round_manifest.instrument, account_feed_hash=round_manifest.account_feed_hash,
            observed_at=at, received_at=at + timedelta(milliseconds=100),
            bid=Decimal("104"), ask=Decimal("106"), status="tradeable", source_key=f"entry-{seconds}",
        )
        conversion = FXConversion(
            from_currency="EUR", to_currency="GBP", position_value=Decimal("0.85"),
            account_gain=Decimal("0.84"), account_loss=Decimal("0.86"), observed_at=item.received_at,
        )
        service.on_quote(item, conversion=conversion)
    assert service.runtime.account.positions
    next_day = current + timedelta(days=1)
    clock = [current]

    class NextDayFeed(FakeFeed):
        def price_lines(self, instruments):
            clock[0] = next_day
            yield json.dumps({
                "type": "PRICE", "instrument": "DE30_EUR", "time": next_day.isoformat(),
                "tradeable": True, "bids": [{"price": "105"}], "asks": [{"price": "107"}],
            })

    run_paper_indicator(
        tmp_path, account_id="private-account", token="private-token", feed=NextDayFeed(), now=lambda: clock[0],
    )
    captured = (round_dir / "capture" / "quotes.jsonl").read_text()
    assert next_day.isoformat() in captured
    assert json.loads((tmp_path / "experimental-summary.json").read_text())["round_id"] == round_manifest.round_id
    later = next_day + timedelta(minutes=1)
    clock[0] = later

    class RestartFeed(FakeFeed):
        def price_lines(self, instruments):
            yield json.dumps({
                "type": "PRICE", "instrument": "DE30_EUR", "time": later.isoformat(),
                "tradeable": True, "bids": [{"price": "106"}], "asks": [{"price": "108"}],
            })

    run_paper_indicator(
        tmp_path, account_id="private-account", token="private-token", feed=RestartFeed(), now=lambda: clock[0],
    )
    assert later.isoformat() in (round_dir / "capture" / "quotes.jsonl").read_text()
    from src.intraday.experimental_round import register_experimental_round

    next_round = round_manifest.model_copy(update={
        "round_id": "de30-2026-10-09-a",
        "session_open": round_manifest.session_open + timedelta(days=1),
        "session_close": round_manifest.session_close + timedelta(days=1),
    })
    register_experimental_round(
        tmp_path, next_round, cost_evidence=(round_dir / "cost-evidence.bin").read_bytes(),
        at=current - timedelta(hours=1),
    )
    original_on_quote = ExperimentalPaperService.on_quote

    def close_prior_round(self, item, *, conversion):
        if self.manifest.round_id == round_manifest.round_id and item.received_at > later:
            conversion = FXConversion(
                from_currency="EUR", to_currency="GBP", position_value=Decimal("0.85"),
                account_gain=Decimal("0.84"), account_loss=Decimal("0.86"), observed_at=item.received_at,
            )
        return original_on_quote(self, item, conversion=conversion)

    monkeypatch.setattr(ExperimentalPaperService, "on_quote", close_prior_round)
    close_at = later + timedelta(minutes=1)
    new_round_quote_at = close_at + timedelta(minutes=1)
    clock[0] = close_at

    class CloseThenContinueFeed(FakeFeed):
        def price_lines(self, instruments):
            for at in (close_at, new_round_quote_at):
                clock[0] = at
                yield json.dumps({
                    "type": "PRICE", "instrument": "DE30_EUR", "time": at.isoformat(),
                    "tradeable": True, "bids": [{"price": "106"}], "asks": [{"price": "108"}],
                })

    run_paper_indicator(
        tmp_path, account_id="private-account", token="private-token", feed=CloseThenContinueFeed(),
        now=lambda: clock[0],
    )
    retained_events = (round_dir / "paper" / "events.jsonl").read_text().splitlines()
    assert any(json.loads(line)["kind"] == "closed" for line in retained_events)
    next_capture = tmp_path / "ExperimentalRounds" / next_round.round_id / "capture" / "quotes.jsonl"
    assert new_round_quote_at.isoformat() in next_capture.read_text()
    assert json.loads((tmp_path / "experimental-summary.json").read_text())["round_id"] == next_round.round_id


def test_no_registered_round_keeps_old_files_unchanged(tmp_path):
    run_paper_indicator(
        tmp_path,
        account_id="private-account",
        token="private-token",
        feed=FakeFeed(),
        now=lambda: datetime(2026, 10, 8, 9, tzinfo=UTC),
    )
    assert not (tmp_path / "experimental-summary.json").exists()
    assert json.loads((tmp_path / "summary.json").read_text())["schema_version"] == 1


def test_practice_runner_publishes_retained_account_capture_quality(tmp_path):
    from datetime import timedelta

    start = datetime(2026, 10, 8, 9, tzinfo=UTC)
    clock = [start]

    class QualityFeed(FakeFeed):
        def price_lines(self, instruments):
            for minute in (1, 6):
                clock[0] = start + timedelta(minutes=minute)
                yield json.dumps(
                    {
                        "type": "PRICE",
                        "instrument": "DE30_EUR",
                        "time": clock[0].isoformat(),
                        "tradeable": True,
                        "bids": [{"price": "24000"}],
                        "asks": [{"price": "24002"}],
                    }
                )

    run_paper_indicator(
        tmp_path, account_id="private-account", token="private-token", feed=QualityFeed(), now=lambda: clock[0]
    )
    quality = json.loads((tmp_path / "capture_quality.json").read_text())
    assert quality["price_scope"] == "account_stream_observation"
    assert quality["markets"][0]["broker_symbol"] == "DE30_EUR"
    assert quality["markets"][0]["expected_intervals"] == 25
    assert quality["markets"][0]["covered_intervals"] == 1
    assert quality["markets"][0]["paper_eligible"] is False
    assert (tmp_path / "2026-10-08" / "capture_quality.json").exists()


def test_practice_inventory_retains_exact_fractional_trade_size(tmp_path):
    class FractionalFeed(FakeFeed):
        def available_instruments(self):
            return ({**super().available_instruments()[0], "minimumTradeSize": "0.01", "tradeUnitsPrecision": 2},)

    run_paper_indicator(
        tmp_path,
        account_id="private-account",
        token="private-token",
        feed=FractionalFeed(),
        now=lambda: datetime(2026, 10, 8, 9, tzinfo=UTC),
    )
    product = json.loads((tmp_path / "inventory.json").read_text())["products"][0]
    assert product["minimumTradeSize"] == "0.01"
    assert product["tradeUnitsPrecision"] == 2


def test_practice_boundary_has_no_order_route_or_live_host():
    source = inspect.getsource(OandaPracticeFeed)
    assert PRACTICE_API == "https://api-fxpractice.oanda.com"
    assert PRACTICE_STREAM == "https://stream-fxpractice.oanda.com"
    assert "/orders" not in source
    assert "api-fxtrade.oanda.com" not in source


def test_default_watch_round_observes_both_directions_without_selecting_a_paper_rule():
    from decimal import Decimal

    from src.intraday.contracts import InstrumentSpec

    instrument = InstrumentSpec(
        provider="oanda_practice",
        broker_symbol="SPX500_USD",
        market="us500",
        product="cfd",
        quote_currency="USD",
        point_value=Decimal(1),
    )
    round_manifest = entry._session(datetime(2026, 10, 8, 9, tzinfo=UTC), (instrument,), "private-account")
    rule = round_manifest.rules["SPX500_USD"]
    assert rule.direction == "both"
    assert rule.selection_hash == "0" * 64


def test_existing_daily_rule_identity_is_not_rewritten_by_new_watch_defaults(tmp_path):
    from decimal import Decimal

    from src.intraday.contracts import InstrumentSpec
    from src.intraday.live_service import LiveRoundManifest, LiveRule, LiveSessionWindow
    from src.intraday.session_journal import SessionJournal

    at = datetime(2026, 10, 8, 9, tzinfo=UTC)
    instrument = InstrumentSpec(
        provider="oanda_practice",
        broker_symbol="DE30_EUR",
        market="germany40",
        product="cfd",
        quote_currency="EUR",
        point_value=Decimal(1),
    )
    legacy = LiveRoundManifest(
        round_id="diagnostic-2026-10-08",
        account_feed_hash=entry.hashlib.sha256(b"private-account").hexdigest(),
        instruments=(instrument,),
        rules={
            instrument.broker_symbol: LiveRule(strategy_id="trend_pullback", direction="long", selection_hash="0" * 64)
        },
        sessions={
            instrument.broker_symbol: LiveSessionWindow(
                opened_at=at.replace(hour=7),
                closed_at=at.replace(hour=20),
            )
        },
    )
    daily = tmp_path / "2026-10-08"
    SessionJournal(daily, legacy.model_dump(mode="json"))
    before = (daily / "live_round.json").read_bytes()
    run_paper_indicator(
        tmp_path,
        account_id="private-account",
        token="private-token",
        feed=FakeFeed(),
        now=lambda: at,
    )
    assert (daily / "live_round.json").read_bytes() == before
    assert json.loads((tmp_path / "summary.json").read_text())["feed_health"] == "healthy"


def test_previous_day_manifest_in_current_directory_is_rejected(tmp_path):
    from decimal import Decimal

    from src.intraday.contracts import InstrumentSpec
    from src.intraday.session_journal import SessionJournal

    at = datetime(2026, 10, 8, 9, tzinfo=UTC)
    instrument = InstrumentSpec(
        provider="oanda_practice",
        broker_symbol="DE30_EUR",
        market="germany40",
        product="cfd",
        quote_currency="EUR",
        point_value=Decimal(1),
    )
    wrong_day = entry._session(at - timedelta(days=1), (instrument,), "private-account")
    SessionJournal(tmp_path / "2026-10-08", wrong_day.model_dump(mode="json"))
    with pytest.raises(ValueError, match="retained diagnostic round identity"):
        entry._session_for_day(tmp_path, at, (instrument,), "private-account")


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
