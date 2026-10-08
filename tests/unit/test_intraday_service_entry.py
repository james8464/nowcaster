import inspect
import json
from datetime import UTC, datetime

from scripts.intraday_service_entry import run_paper_indicator
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
