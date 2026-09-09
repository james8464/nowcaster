"""Account hand calculations and causal evidence checks; no historical data runs."""

from copy import deepcopy
from decimal import ROUND_DOWN, Decimal, localcontext
from pathlib import Path

import pandas as pd
import pytest

from src.research.historical_replay import HistoricalReplay
from src.research.holding_period_search import eligible_long_signals
from src.research.opportunity_audit import gap_safe_atr
from src.strategies.registry import StrategyRegistry
from src.strategies.types import BarInterval, StrategyFamily, StrategySpec, canonical_hash

D = Decimal


def bar(index, **changes):
    opened = pd.Timestamp("2025-01-01T00:00:00Z") + pd.Timedelta(hours=index)
    row = dict(
        provider="binance",
        feed="spot",
        symbol="BTCUSDT",
        interval="1h",
        open_timestamp=opened,
        close_timestamp=opened + pd.Timedelta(hours=1),
        available_at=opened + pd.Timedelta(hours=1),
        revision=1,
        finalized=True,
        open=100,
        high=100.5,
        low=99.5,
        close=100,
        volume=1000,
    )
    return row | changes


def setup(*, signals=(13,), symbol="BTCUSDT", **changes):
    registry = StrategyRegistry()
    spec = StrategySpec(
        strategy_id="test_causal",
        family=StrategyFamily.TREND,
        version="1",
        intervals=(BarInterval.ONE_HOUR,),
        warmup_bars=2,
        parameters={},
    )

    def generate(_spec, bars, _context):
        indices = (
            (pd.to_datetime(bars.open_timestamp, utc=True) - pd.Timestamp("2025-01-01T00:00:00Z"))
            / pd.Timedelta(hours=1)
        ).astype(int)
        return pd.DataFrame(
            dict(
                decision_timestamp=bars.close_timestamp,
                data_through=bars.close_timestamp,
                signal=indices.isin(signals).astype(int),
                strength=1.0,
                reason=[f"original reason {i}" for i in indices],
            )
        )

    registry.register(spec, generate)
    candidate = (
        dict(
            symbol=symbol,
            strategy_id=spec.strategy_id,
            strategy_definition_hash=spec.definition_hash,
            stop_atr=1,
            target_atr=1.5,
            maximum_bars=12,
            screen_passed=False,
        )
        | changes
    )
    return candidate, registry


def primed(**kwargs):
    candidate, registry = setup(**kwargs)
    events = []
    replay = HistoricalReplay(candidate, registry, on_event=events.append)
    for i in range(14):
        replay.push_bar(bar(i, symbol=candidate["symbol"]))
    return replay, events


def test_idle_cash_and_original_pending_plan_and_unforced_ending():
    replay, events = primed(signals=(13, 14))
    pending = replay.result()["scenarios"][0]["pending_entry"]
    assert pending["reason"] == "original reason 13"
    assert pending["atr"] == "1.0"
    assert pending["expires_at"] == "2025-01-02T02:00:00+00:00"
    assert replay.result()["scenarios"][0]["cash"] == "10000"
    replay.push_bar(bar(14))
    result = replay.result()
    position = result["scenarios"][0]["open_position"]
    assert position["decision"]["decision_id"] == pending["decision_id"]
    assert position["decision"]["reason"] == "original reason 13"
    assert result["scenarios"][0]["pending_entry"] is None
    assert result["scenarios"][0]["trades"] == []
    assert replay.result() == result
    assert any(e["type"] == "decision" for e in events)


@pytest.mark.parametrize("multiplier", [1, 2])
def test_hand_calculated_entry_and_stop_costs(multiplier):
    replay, _ = primed()
    replay.push_bar(bar(14, low=98))
    account = replay.result()["scenarios"][multiplier - 1]
    trade = account["trades"][0]
    spread, slip, fee = D("0.0002") * multiplier, D("0.0005") * multiplier, D("0.001") * multiplier
    entry = D(100) * (1 + spread) * (1 + slip)
    stop = entry - 1
    sold = stop * (1 - spread) * (1 - slip)
    unit_cost = entry * (1 + fee)
    quantity = (min(D(2500) / unit_cost, D(25) / (unit_cost - sold * (1 - fee))) / D("0.00001")).to_integral_value(
        rounding=ROUND_DOWN
    ) * D("0.00001")
    entry_cash = quantity * unit_cost
    exit_cash = quantity * sold * (1 - fee)
    assert D(trade["entry_price"]) == entry
    assert D(trade["quantity"]) == quantity
    assert D(trade["entry_cash_flow"]) == -entry_cash
    assert D(trade["exit_cash_flow"]) == exit_cash
    assert D(trade["net_pnl"]) == exit_cash - entry_cash
    assert D(account["cash"]) == 10000 - entry_cash + exit_cash
    assert D(account["fees"]) == quantity * (entry + sold) * fee
    assert D(account["spread_cost"]) == quantity * (D(100) + stop) * spread
    assert D(account["slippage_cost"]) == quantity * (D(100) * (1 + spread) + stop * (1 - spread)) * slip
    assert trade["exit_reason"] == "stop"
    assert trade["exit_close_timestamp"] == "2025-01-01T15:00:00+00:00"


@pytest.mark.parametrize(
    "changes,reason,reference,ambiguous",
    [
        ({"high": 103}, "target", D("101.570010"), False),
        ({"high": 103, "low": 98}, "stop", D("99.070010"), True),
        ({"open": 95, "low": 94, "high": 100}, "stop", D(95), False),
        ({"open": 105, "high": 106, "low": 100}, "target", D("101.570010"), False),
    ],
)
def test_exit_precedence_and_opening_gaps(changes, reason, reference, ambiguous):
    replay, _ = primed()
    replay.push_bar(bar(14))
    replay.push_bar(bar(15, **changes))
    account = replay.result()["scenarios"][0]
    trade = account["trades"][0]
    assert trade["exit_reason"] == reason
    assert D(trade["exit_reference"]) == reference
    assert trade["ambiguous"] is ambiguous
    assert account["counts"]["ambiguous_exits"] == int(ambiguous)


def test_expiry_is_original_signal_close_and_beats_target_after_stop_check():
    replay, _ = primed(signals=tuple(range(13, 30)))
    for i in range(14, 25):
        replay.push_bar(bar(i))
    assert replay.result()["scenarios"][0]["trades"] == []
    replay.push_bar(bar(25, high=103, close=100.2))
    trade = replay.result()["scenarios"][0]["trades"][0]
    assert trade["exit_reason"] == "expiry"
    assert D(trade["exit_reference"]) == D("100.2")
    assert trade["decision"]["reason"] == "original reason 13"


def test_missing_interval_cancels_pending_and_preserves_held_gap_loss():
    pending, _ = primed()
    pending.push_bar(bar(16))
    assert pending.result()["scenarios"][0]["counts"]["cancellations"] == 1
    held, _ = primed()
    held.push_bar(bar(14))
    held.push_bar(bar(17, open=90, high=91, low=89, close=90))
    result = held.result()["scenarios"][0]
    trade = result["trades"][0]
    assert trade["exit_reason"] == "gap_liquidation"
    assert trade["tainted"] is True
    assert D(trade["exit_reference"]) == 90
    assert D(result["realized_pnl"]) < -100
    assert result["counts"]["gap_tainted_exits"] == 1


def test_later_entry_eligibility_and_minimum_notional_rejections():
    replay, _ = primed()
    replay.push_bar(bar(14, open=1000, high=1001, low=999, close=1000))
    assert replay.result()["scenarios"][0]["counts"]["rejections"] == 1
    tiny, _ = primed(stop_atr=100000)
    tiny.push_bar(bar(14))
    assert tiny.result()["scenarios"][0]["counts"]["rejections"] == 1
    assert tiny.result()["scenarios"][0]["cash"] == "10000"


def test_successive_entry_cash_cap_shrinks_after_loss():
    replay, _ = primed(signals=(13, 14), stop_atr=0.1)
    replay.push_bar(bar(14, low=98))
    first = replay.result()["scenarios"][0]["trades"][0]
    replay.push_bar(bar(15, low=98))
    second = replay.result()["scenarios"][0]["trades"][1]
    assert D(second["quantity"]) < D(first["quantity"])
    cash_before = D(10000) + D(first["net_pnl"])
    assert -D(second["entry_cash_flow"]) <= cash_before * D(".25")


@pytest.mark.parametrize(
    "changes",
    [
        {"available_at": "2025-01-01T16:00:00Z"},
        {"revision": 2},
        {"finalized": False},
        {"provider": "other"},
        {"feed": "futures"},
        {"symbol": "ETHUSDT"},
        {"interval": "5m"},
        {"open": 0},
        {"high": float("inf")},
        {"low": 101},
        {"volume": -1},
        {"open_timestamp": "2025-01-01T14:00:00"},
        {"close_timestamp": "2025-01-01T16:00:00Z"},
    ],
)
def test_invalid_bars_fail_without_rewriting_evidence(changes):
    replay, events = primed()
    before, evidence = replay.result(), deepcopy(events)
    with pytest.raises(ValueError):
        replay.push_bar(bar(14, **changes))
    assert replay.result() == before
    assert events == evidence


def test_future_append_perturbation_revision_and_entry_evidence_isolation():
    first, first_events = primed()
    second, second_events = primed()
    prefix = deepcopy(first_events)
    snapshot = first.result()
    first.push_bar(bar(14))
    second.push_bar(bar(14, high=10000, low=1, close=7000, volume=0))
    assert first_events[: len(prefix)] == second_events[: len(prefix)] == prefix
    first_entry = [e for e in first_events if e["type"] == "entry"]
    second_entry = [e for e in second_events if e["type"] == "entry"]
    assert first_entry == second_entry
    assert snapshot["bars_seen"] == 14
    assert snapshot["scenarios"][0]["open_position"] is None
    before = deepcopy(first_events)
    with pytest.raises(ValueError):
        first.push_bar(bar(13, revision=2, close=100.1))
    assert first_events == before
    for event in first_events:
        assert event["hash"] == canonical_hash({k: v for k, v in event.items() if k != "hash"})
    assert all(a["hash"] == b["previous_hash"] for a, b in zip(first_events, first_events[1:], strict=False))


def test_result_and_callback_mutations_do_not_change_internal_state():
    replay, events = primed()
    before = replay.result()
    result = replay.result()
    result["candidate"]["stop_atr"] = 999
    result["scenarios"][0]["pending_entry"]["atr"] = "999"
    events[-1]["payload"].clear()
    assert replay.result() == before


def test_result_decimal_values_do_not_depend_on_callers_precision():
    replay, _ = primed()
    replay.push_bar(bar(14))
    before = replay.result()
    with localcontext() as context:
        context.prec = 6
        assert replay.result() == before


@pytest.mark.parametrize("symbol,lot", [("BTCUSDT", "0.00001"), ("ETHUSDT", "0.0001")])
def test_explicit_lots_and_marked_equity_charge_unsold_exit_costs(symbol, lot):
    replay, _ = primed(symbol=symbol)
    replay.push_bar(bar(14, symbol=symbol))
    account = replay.result()["scenarios"][0]
    quantity = D(account["open_position"]["quantity"])
    assert quantity % D(lot) == 0
    liquidation = quantity * D(100) * D(".9998") * D(".9995") * D(".999")
    assert D(account["marked_liquidation_value"]) == liquidation
    equity = D(account["cash"]) + liquidation
    assert D(account["marked_equity"]) == equity
    assert D(account["maximum_drawdown"]) == (D(10000) - equity) / 10000
    assert account["realized_pnl"] == "0"
    assert account["trades"] == []


def test_zero_lot_rejected_without_backfill_or_cash_change():
    candidate, registry = setup()
    events = []
    replay = HistoricalReplay(candidate, registry, on_event=events.append)
    for i in range(15):
        replay.push_bar(bar(i, open=1e9, close=1e9, high=1.1e9, low=0.9e9))
    account = replay.result()["scenarios"][0]
    assert account["cash"] == "10000"
    assert account["open_position"] is None
    assert account["pending_entry"] is None
    assert account["counts"]["rejections"] == 1
    assert [e["payload"]["reason"] for e in events if e["type"] == "rejection"] == [
        "minimum_notional",
        "minimum_notional",
    ]


def test_appended_changed_suffix_preserves_closed_trade_valuation_and_decision_records():
    replay, events = primed()
    replay.push_bar(bar(14, high=103))
    before, evidence = replay.result(), deepcopy(events)
    for i in range(15, 19):
        replay.push_bar(bar(i, open=5000, high=9000, low=10, close=20, volume=0))
    after = replay.result()
    assert events[: len(evidence)] == evidence
    assert after["scenarios"][0]["trades"] == before["scenarios"][0]["trades"]
    assert after["scenarios"][0]["equity_curve"][:15] == before["scenarios"][0]["equity_curve"]
    for revision in (1, 2):
        head = replay.result()["journal_head"]
        with pytest.raises(ValueError):
            replay.push_bar(bar(14, revision=revision, high=103))
        assert replay.result()["journal_head"] == head


def test_stop_precedes_expiry_in_expiry_bar():
    replay, _ = primed()
    for i in range(14, 25):
        replay.push_bar(bar(i))
    replay.push_bar(bar(25, high=103, low=98))
    assert replay.result()["scenarios"][0]["trades"][0]["exit_reason"] == "stop"


def test_configured_generators_match_direct_prefixes_with_gap_and_window_cap():
    import json

    from src.config.settings import Settings
    from src.strategies.library import build_strategy_registry

    root = Path(__file__).resolve().parents[2]
    registry = build_strategy_registry(Settings.load(root, mode="live").strategies.enabled)
    candidates = json.loads((root / "data/research/holding-period-search-2026-09-08.json").read_text())["candidates"]
    for candidate in candidates:
        events = []
        replay = HistoricalReplay(candidate, registry, on_event=events.append)
        rows = []
        for i in range(1002):
            price = 100 + i * 0.01 + (i % 13) * 0.1
            rows.append(
                bar(
                    i + (2 if i >= 40 else 0),
                    symbol=candidate["symbol"],
                    open=price,
                    high=price + 1,
                    low=price - 1,
                    close=price + 0.2,
                )
            )
            replay.push_bar(rows[-1])
            if i not in (13, 39, 40, 70, 999, 1001):
                continue
            frame = pd.DataFrame(rows[-1000:])
            direct = eligible_long_signals(frame, candidate, registry).iloc[-1]
            decision = [e["payload"] for e in events if e["type"] == "decision"][-1]
            assert decision["signal"] == int(direct.signal)
            assert decision["reason"] == direct.reason
            expected_atr = gap_safe_atr(frame).iloc[-1]
            assert decision["atr"] == (str(expected_atr) if pd.notna(expected_atr) else None)
            assert decision["input_window"]["bars"] == min(i + 1, 1000)
            assert decision["input_window"]["first_open_timestamp"] == frame.iloc[0].open_timestamp.isoformat()
