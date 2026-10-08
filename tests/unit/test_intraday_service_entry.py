import json
import inspect
from datetime import UTC, datetime

from scripts.intraday_service_entry import run_paper_indicator
from src.intraday.oanda_practice import OandaPracticeFeed, PRACTICE_API, PRACTICE_STREAM


class FakeFeed:
    def available_instruments(self):
        return ({"name": "DE30_EUR", "type": "CFD", "displayName": "Germany 30", "marginRate": "0.05"},)

    def price_lines(self, instruments):
        assert [item.broker_symbol for item in instruments] == ["DE30_EUR"]
        yield json.dumps({"type": "PRICE", "instrument": "DE30_EUR", "time": "2026-10-08T09:00:00Z",
                          "tradeable": True, "bids": [{"price": "24000"}], "asks": [{"price": "24002"}]})


def test_practice_runner_keeps_token_and_account_out_of_files(tmp_path):
    run_paper_indicator(tmp_path, account_id="private-account", token="private-token",
                        feed=FakeFeed(), now=lambda: datetime(2026, 10, 8, 9, tzinfo=UTC))
    text = "\n".join(path.read_text() for path in tmp_path.rglob("*") if path.is_file())
    assert "private-token" not in text
    assert "private-account" not in text
    assert "Germany 30" in text
    assert "no_trade_reason" in text


def test_practice_boundary_has_no_order_route_or_live_host():
    source = inspect.getsource(OandaPracticeFeed)
    assert PRACTICE_API == "https://api-fxpractice.oanda.com"
    assert PRACTICE_STREAM == "https://stream-fxpractice.oanda.com"
    assert "/orders" not in source
    assert "api-fxtrade.oanda.com" not in source
