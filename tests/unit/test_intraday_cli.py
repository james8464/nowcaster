import json
from datetime import UTC, datetime
from decimal import Decimal as D

import httpx

from scripts import run_intraday_research as cli
from src.intraday.contracts import InstrumentSpec, MarketQuote
from src.intraday.desk import DeskStatus


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

        def on_quote(self, quote):
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
    monkeypatch.setattr(cli, "_runtime_status", lambda _runtime, at, **_kwargs: DeskStatus.unconfigured(at))
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
