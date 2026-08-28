from __future__ import annotations


def test_demo_persists_crypto_instruments_features_and_signals(demo_database) -> None:
    _, database = demo_database
    symbols = set(database.frame("select symbol from instruments where asset_class = 'crypto'")["symbol"])
    assert symbols == {"BTCUSDT", "ETHUSDT"}
    assert database.scalar("select count(*) from crypto_features_daily") > 1_000
    assert database.scalar("select count(*) from market_signals_daily where asset_class = 'crypto'") > 100
    assert database.scalar("select min(direction_probability) from market_signals_daily") >= 0
    assert database.scalar("select max(direction_probability) from market_signals_daily") <= 1
