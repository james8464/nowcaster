import json
from datetime import UTC, datetime
from decimal import Decimal as D

import httpx

from scripts import run_intraday_research as cli
from src.intraday.contracts import InstrumentSpec, MarketQuote
from src.intraday.desk import DeskStatus
from src.intraday.oanda_practice import OandaHomeConversion


def test_stream_run_records_unexpected_early_end_and_no_trade(tmp_path, monkeypatch):
    instrument = InstrumentSpec(
        provider="oanda_practice",
        broker_symbol="DE30_EUR",
        market="germany40",
        product="cfd",
        quote_currency="EUR",
        point_value=D(1),
    )

    class FakeFeed:
        def verify_instrument(self, actual):
            assert actual == instrument
            return {"name": "DE30_EUR", "type": "CFD"}

        def price_lines(self, instruments):
            assert instruments == (instrument,)
            yield json.dumps(
                {
                    "type": "PRICE",
                    "instrument": "DE30_EUR",
                    "time": "2026-10-07T08:00:01Z",
                    "tradeable": True,
                    "bids": [{"price": "100"}],
                    "asks": [{"price": "102"}],
                }
            )

        def parse_price_event(self, actual, line, *, received_at):
            data = json.loads(line)
            return MarketQuote(
                instrument=actual,
                account_feed_hash="a" * 64,
                observed_at=datetime(2026, 10, 7, 8, 0, 1, tzinfo=UTC),
                received_at=received_at,
                bid=D(data["bids"][0]["price"]),
                ask=D(data["asks"][0]["price"]),
                status="tradeable",
                source_key="quote-1",
            )

    monkeypatch.setattr(cli, "_feed", lambda: FakeFeed())
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 10, 7, 8, 0, 1, 100000, tzinfo=UTC))
    costs = tmp_path / "costs.json"
    costs.write_text(
        json.dumps(
            {
                "verified": True,
                "slippage_points": "0.5",
                "commission_per_unit": "0.1",
                "financing_per_unit": "0",
                "source": "user-attested demo terms",
            }
        )
    )
    result = cli.main(
        [
            "--directory",
            str(tmp_path / "research"),
            "run",
            "--candidate",
            "germany40:DE30_EUR:EUR:1",
            "--strategy",
            "opening_range_15",
            "--session-open",
            "2026-10-07T08:00:00Z",
            "--session-close",
            "2026-10-07T09:00:00Z",
            "--account-currency",
            "EUR",
            "--costs-file",
            str(costs),
            "--protocol-hash",
            "b" * 64,
        ]
    )
    assert result == 2
    status = DeskStatus.model_validate_json((tmp_path / "research" / "summary.json").read_bytes())
    assert status.feed_health == "error"
    assert status.opportunities == ()
    assert status.evidence_status == "not_supported"
    assert "provider_error" in (tmp_path / "research" / "studies" / ("b" * 64) / "events.jsonl").read_text()


def test_inventory_shows_product_names_without_account_secret(tmp_path, monkeypatch, capsys):
    class FakeFeed:
        def available_instruments(self):
            return (
                {
                    "name": "DE30_EUR",
                    "displayName": "Germany 40",
                    "type": "CFD",
                    "marginRate": "0.05",
                    "accountID": "sensitive-account",
                },
            )

    monkeypatch.setattr(cli, "_feed", lambda: FakeFeed())
    assert cli.main(["--directory", str(tmp_path / "research"), "inventory"]) == 0
    output = capsys.readouterr().out
    assert "DE30_EUR" in output
    assert "sensitive-account" not in output


def test_stream_failure_publishes_error_and_retains_event(tmp_path, monkeypatch, capsys):
    class FailedFeed:
        def verify_instrument(self, _instrument):
            return {"name": "DE30_EUR", "type": "CFD"}

        def price_lines(self, _instruments):
            raise httpx.ReadTimeout("secret account metadata")
            yield ""  # pragma: no cover

    monkeypatch.setattr(cli, "_feed", lambda: FailedFeed())
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 10, 7, 8, 0, 1, tzinfo=UTC))
    costs = tmp_path / "costs.json"
    costs.write_text(
        json.dumps(
            {
                "verified": True,
                "slippage_points": "0.5",
                "commission_per_unit": "0.1",
                "financing_per_unit": "0",
                "source": "user-attested demo terms",
            }
        )
    )
    directory = tmp_path / "research"
    result = cli.main(
        [
            "--directory",
            str(directory),
            "run",
            "--candidate",
            "germany40:DE30_EUR:EUR:1",
            "--strategy",
            "opening_range_15",
            "--session-open",
            "2026-10-07T08:00:00Z",
            "--session-close",
            "2026-10-07T09:00:00Z",
            "--account-currency",
            "EUR",
            "--costs-file",
            str(costs),
            "--protocol-hash",
            "b" * 64,
        ]
    )
    assert result == 2
    status = DeskStatus.model_validate_json((directory / "summary.json").read_bytes())
    assert status.feed_health == "error"
    assert status.opportunities == ()
    journal = (directory / "studies" / ("b" * 64) / "events.jsonl").read_text()
    assert "provider_error" in journal
    assert "secret account metadata" not in journal + capsys.readouterr().err


def test_pre_session_price_is_ignored_and_early_stream_end_fails_closed(tmp_path, monkeypatch):
    class EarlyFeed:
        def verify_instrument(self, _instrument):
            return {"name": "DE30_EUR", "type": "CFD"}

        def price_lines(self, _instruments):
            yield '{"type":"PRICE","time":"2026-10-07T07:59:59Z"}'

        def parse_price_event(self, *_args, **_kwargs):
            raise AssertionError("pre-session price must not enter the bar builder")

    monkeypatch.setattr(cli, "_feed", lambda: EarlyFeed())
    monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 10, 7, 7, 59, 59, tzinfo=UTC))
    costs = tmp_path / "costs.json"
    costs.write_text(
        json.dumps(
            {
                "verified": True,
                "slippage_points": "0.5",
                "commission_per_unit": "0.1",
                "financing_per_unit": "0",
                "source": "user-attested demo terms",
            }
        )
    )
    directory = tmp_path / "research"
    assert (
        cli.main(
            [
                "--directory",
                str(directory),
                "run",
                "--candidate",
                "germany40:DE30_EUR:EUR:1",
                "--strategy",
                "opening_range_15",
                "--session-open",
                "2026-10-07T08:00:00Z",
                "--session-close",
                "2026-10-07T09:00:00Z",
                "--account-currency",
                "EUR",
                "--costs-file",
                str(costs),
                "--protocol-hash",
                "b" * 64,
            ]
        )
        == 2
    )
    assert DeskStatus.model_validate_json((directory / "summary.json").read_bytes()).feed_health == "error"


def test_session_close_quote_reaches_paper_position_before_stream_stops(tmp_path, monkeypatch):
    seen = []

    class ClosingRuntime:
        def __init__(self, **_kwargs):
            self.account = type("Account", (), {"positions": {}})()

        def on_quote(self, quote, conversion=None):
            seen.append(quote)
            return None

    class ClosingFeed:
        def verify_instrument(self, _instrument):
            return {"name": "DE30_EUR", "type": "CFD"}

        def price_lines(self, _instruments):
            yield "closing-price"

        def parse_price_event(self, instrument, _line, *, received_at):
            return MarketQuote(
                instrument=instrument,
                account_feed_hash="a" * 64,
                observed_at=received_at,
                received_at=received_at,
                bid=D(100),
                ask=D(102),
                status="tradeable",
                source_key="close",
            )

    monkeypatch.setattr(cli, "_feed", lambda: ClosingFeed())
    monkeypatch.setattr(cli, "LivePaperRuntime", ClosingRuntime)
    monkeypatch.setattr(
        cli,
        "_runtime_status",
        lambda _runtime, at, *, health, **_kwargs: DeskStatus.unconfigured(at).model_copy(
            update={"feed_health": health}
        ),
    )
    before = datetime(2026, 10, 7, 8, 59, 59, tzinfo=UTC)
    after = datetime(2026, 10, 7, 9, 0, 1, tzinfo=UTC)
    times = iter((before, after, after))
    monkeypatch.setattr(cli, "_now", lambda: next(times, after))
    costs = tmp_path / "costs.json"
    costs.write_text(
        json.dumps(
            {
                "verified": True,
                "slippage_points": "0.5",
                "commission_per_unit": "0.1",
                "financing_per_unit": "0",
                "source": "user-attested demo terms",
            }
        )
    )
    result = cli.main(
        [
            "--directory",
            str(tmp_path / "research"),
            "run",
            "--candidate",
            "germany40:DE30_EUR:EUR:1",
            "--strategy",
            "opening_range_15",
            "--session-open",
            "2026-10-07T08:00:00Z",
            "--session-close",
            "2026-10-07T09:00:00Z",
            "--account-currency",
            "EUR",
            "--costs-file",
            str(costs),
            "--protocol-hash",
            "b" * 64,
        ]
    )
    assert result == 0
    assert len(seen) == 1
    assert seen[0].received_at == after


def test_foreign_currency_stream_passes_account_factors_into_paper_runtime(tmp_path, monkeypatch):
    observed = datetime(2026, 10, 7, 8, 0, 0, tzinfo=UTC)
    received = datetime(2026, 10, 7, 8, 0, 1, tzinfo=UTC)
    clock = [received]
    seen = []
    published = []

    class ForeignFeed:
        def verify_instrument(self, _instrument):
            return {"name": "DE30_EUR", "type": "CFD", "tradeUnitsPrecision": 0}

        def home_conversions(self, instruments, *, account_currency):
            assert instruments[0].broker_symbol == "DE30_EUR"
            assert account_currency == "GBP"
            return (OandaHomeConversion("EUR", D("0.84"), D("0.86"), D("0.85"), observed),)

        def price_lines(self, _instruments):
            yield "first"
            clock[0] = received + cli.timedelta(seconds=1)
            yield "second"

        def parse_price_event(self, instrument, _line, *, received_at):
            return MarketQuote(
                instrument=instrument,
                account_feed_hash="a" * 64,
                observed_at=received_at,
                received_at=received_at,
                bid=D(100),
                ask=D(101),
                status="tradeable",
                source_key=_line,
            )

    class ForeignRuntime:
        def __init__(self, **_kwargs):
            self.account = type("Account", (), {"positions": {}})()

        def on_quote(self, quote, conversion=None):
            seen.append((quote, conversion))
            return None

        def _append(self, _kind, _at, _payload):
            return None

    monkeypatch.setattr(cli, "_feed", lambda: ForeignFeed())
    monkeypatch.setattr(cli, "LivePaperRuntime", ForeignRuntime)
    monkeypatch.setattr(
        cli,
        "_runtime_status",
        lambda _runtime, at, *, health, **_kwargs: DeskStatus.unconfigured(at).model_copy(
            update={"feed_health": health}
        ),
    )
    monkeypatch.setattr(cli, "_now", lambda: clock[0])
    original_publish = cli._publish

    def capture(directory, status):
        published.append(status)
        original_publish(directory, status)

    monkeypatch.setattr(cli, "_publish", capture)
    costs = tmp_path / "costs.json"
    costs.write_text(
        json.dumps(
            {
                "verified": True,
                "slippage_points": "0.5",
                "commission_per_unit": "0.1",
                "financing_per_unit": "0",
                "source": "user-attested demo terms",
            }
        )
    )
    assert (
        cli.main(
            [
                "--directory",
                str(tmp_path / "research"),
                "run",
                "--candidate",
                "germany40:DE30_EUR:EUR:1",
                "--strategy",
                "opening_range_15",
                "--session-open",
                "2026-10-07T08:00:00Z",
                "--session-close",
                "2026-10-07T09:00:00Z",
                "--account-currency",
                "GBP",
                "--costs-file",
                str(costs),
                "--protocol-hash",
                "b" * 64,
            ]
        )
        == 2
    )
    assert len(seen) == 2
    assert seen[0][1] is None
    assert seen[1][1].position_value == D("0.85")
    assert seen[1][1].account_gain == D("0.84")
    assert seen[1][1].account_loss == D("0.86")
    assert published[0].feed_health == "stale"
    assert published[1].feed_health == "healthy"


def test_foreign_currency_stream_marks_stale_conversion_as_unhealthy(tmp_path, monkeypatch):
    received = datetime(2026, 10, 7, 8, 0, 20, tzinfo=UTC)
    published = []
    gaps = []

    class StaleFeed:
        def verify_instrument(self, _instrument):
            return {"name": "DE30_EUR", "type": "CFD", "tradeUnitsPrecision": 0}

        def home_conversions(self, _instruments, *, account_currency):
            assert account_currency == "GBP"
            return (
                OandaHomeConversion(
                    "EUR",
                    D("0.84"),
                    D("0.86"),
                    D("0.85"),
                    received.replace(second=0),
                ),
            )

        def price_lines(self, _instruments):
            yield "price"

        def parse_price_event(self, instrument, _line, *, received_at):
            return MarketQuote(
                instrument=instrument,
                account_feed_hash="a" * 64,
                observed_at=received,
                received_at=received_at,
                bid=D(100),
                ask=D(101),
                status="tradeable",
                source_key="price-stale-fx",
            )

    class StaleRuntime:
        def __init__(self, **_kwargs):
            self.account = type("Account", (), {"positions": {}})()

        def on_quote(self, _quote, conversion=None):
            return None

        def _append(self, _kind, _at, _payload):
            gaps.append((_kind, _payload))
            return None

    monkeypatch.setattr(cli, "_feed", lambda: StaleFeed())
    monkeypatch.setattr(cli, "LivePaperRuntime", StaleRuntime)
    monkeypatch.setattr(
        cli,
        "_runtime_status",
        lambda _runtime, at, *, health, **_kwargs: DeskStatus.unconfigured(at).model_copy(
            update={"feed_health": health}
        ),
    )
    monkeypatch.setattr(cli, "_now", lambda: received)
    original_publish = cli._publish

    def capture(directory, status):
        published.append(status)
        original_publish(directory, status)

    monkeypatch.setattr(cli, "_publish", capture)
    costs = tmp_path / "costs.json"
    costs.write_text(
        json.dumps(
            {
                "verified": True,
                "slippage_points": "0.5",
                "commission_per_unit": "0.1",
                "financing_per_unit": "0",
                "source": "user-attested demo terms",
            }
        )
    )
    assert (
        cli.main(
            [
                "--directory",
                str(tmp_path / "research"),
                "run",
                "--candidate",
                "germany40:DE30_EUR:EUR:1",
                "--strategy",
                "opening_range_15",
                "--session-open",
                "2026-10-07T08:00:00Z",
                "--session-close",
                "2026-10-07T09:00:00Z",
                "--account-currency",
                "GBP",
                "--costs-file",
                str(costs),
                "--protocol-hash",
                "b" * 64,
            ]
        )
        == 2
    )
    assert published[0].feed_health == "stale"
    assert ("feed_gap", {"reason": "account_conversion_stale", "source_key": "price-stale-fx"}) in gaps


def test_conversion_fetched_after_quote_is_used_only_by_later_quotes(tmp_path, monkeypatch):
    start = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
    clock = [start]
    seen = []

    class LaterConversionFeed:
        def verify_instrument(self, _instrument):
            return {"name": "DE30_EUR", "type": "CFD", "tradeUnitsPrecision": 0}

        def home_conversions(self, _instruments, *, account_currency):
            assert account_currency == "GBP"
            return (
                OandaHomeConversion(
                    "EUR",
                    D("0.84"),
                    D("0.86"),
                    D("0.85"),
                    start + cli.timedelta(seconds=1, milliseconds=500),
                ),
            )

        def price_lines(self, _instruments):
            clock[0] = start + cli.timedelta(seconds=1)
            yield "first"
            clock[0] = start + cli.timedelta(seconds=2)
            yield "second"

        def parse_price_event(self, instrument, line, *, received_at):
            return MarketQuote(
                instrument=instrument,
                account_feed_hash="a" * 64,
                observed_at=received_at,
                received_at=received_at,
                bid=D(100),
                ask=D(101),
                status="tradeable",
                source_key=line,
            )

    class CapturingRuntime:
        def __init__(self, **_kwargs):
            self.account = type("Account", (), {"positions": {}})()

        def on_quote(self, quote, conversion=None):
            seen.append((quote.source_key, conversion))
            return None

        def _append(self, _kind, _at, _payload):
            return None

    monkeypatch.setattr(cli, "_feed", lambda: LaterConversionFeed())
    monkeypatch.setattr(cli, "LivePaperRuntime", CapturingRuntime)
    monkeypatch.setattr(cli, "_runtime_status", lambda _runtime, at, **_kwargs: DeskStatus.unconfigured(at))
    monkeypatch.setattr(cli, "_now", lambda: clock[0])
    costs = tmp_path / "costs.json"
    costs.write_text(
        json.dumps(
            {
                "verified": True,
                "slippage_points": "0.5",
                "commission_per_unit": "0.1",
                "financing_per_unit": "0",
                "source": "user-attested demo terms",
            }
        )
    )
    assert (
        cli.main(
            [
                "--directory",
                str(tmp_path / "research"),
                "run",
                "--candidate",
                "germany40:DE30_EUR:EUR:1",
                "--strategy",
                "opening_range_15",
                "--session-open",
                "2026-10-07T08:00:00Z",
                "--session-close",
                "2026-10-07T09:00:00Z",
                "--account-currency",
                "GBP",
                "--costs-file",
                str(costs),
                "--protocol-hash",
                "b" * 64,
            ]
        )
        == 2
    )
    assert [key for key, _rate in seen] == ["first", "second"]
    assert seen[0][1] is None
    assert seen[1][1].position_value == D("0.85")


def test_repeated_conversion_refresh_failures_record_one_gap_spell(tmp_path, monkeypatch):
    start = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)
    clock = [start]
    gaps = []

    class FailedConversionFeed:
        def verify_instrument(self, _instrument):
            return {"name": "DE30_EUR", "type": "CFD", "tradeUnitsPrecision": 0}

        def home_conversions(self, _instruments, *, account_currency):
            assert account_currency == "GBP"
            raise ValueError("conversion unavailable")

        def price_lines(self, _instruments):
            for seconds in (1, 7):
                clock[0] = start + cli.timedelta(seconds=seconds)
                yield str(seconds)

        def parse_price_event(self, instrument, line, *, received_at):
            return MarketQuote(
                instrument=instrument,
                account_feed_hash="a" * 64,
                observed_at=received_at,
                received_at=received_at,
                bid=D(100),
                ask=D(101),
                status="tradeable",
                source_key=line,
            )

    class GapRuntime:
        def __init__(self, **_kwargs):
            self.account = type("Account", (), {"positions": {}})()

        def on_quote(self, _quote, conversion=None):
            assert conversion is None
            return None

        def _append(self, kind, _at, payload):
            if kind == "feed_gap":
                gaps.append(payload)
            return None

    monkeypatch.setattr(cli, "_feed", lambda: FailedConversionFeed())
    monkeypatch.setattr(cli, "LivePaperRuntime", GapRuntime)
    monkeypatch.setattr(cli, "_runtime_status", lambda _runtime, at, **_kwargs: DeskStatus.unconfigured(at))
    monkeypatch.setattr(cli, "_now", lambda: clock[0])
    costs = tmp_path / "costs.json"
    costs.write_text(
        json.dumps(
            {
                "verified": True,
                "slippage_points": "0.5",
                "commission_per_unit": "0.1",
                "financing_per_unit": "0",
                "source": "user-attested demo terms",
            }
        )
    )
    assert (
        cli.main(
            [
                "--directory",
                str(tmp_path / "research"),
                "run",
                "--candidate",
                "germany40:DE30_EUR:EUR:1",
                "--strategy",
                "opening_range_15",
                "--session-open",
                "2026-10-07T08:00:00Z",
                "--session-close",
                "2026-10-07T09:00:00Z",
                "--account-currency",
                "GBP",
                "--costs-file",
                str(costs),
                "--protocol-hash",
                "b" * 64,
            ]
        )
        == 2
    )
    assert len(gaps) == 1
    assert gaps[0]["reason"] == "account_conversion_unavailable"
