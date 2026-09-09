"""Causal close-decision / next-open replay; never a historical-vintage claim."""

from __future__ import annotations

import json
import math
from collections import deque
from collections.abc import Callable
from decimal import Context, Decimal, InvalidOperation, localcontext

import pandas as pd

from src.research.historical_replay_account import LOT_INCREMENTS, ReplayAccount, json_values
from src.research.holding_period_search import eligible_long_signals
from src.research.opportunity_audit import gap_safe_atr
from src.strategies.registry import StrategyRegistry
from src.strategies.types import canonical_hash


def _utc(value, field: str) -> pd.Timestamp:
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp) or stamp.tzinfo is None or stamp.utcoffset().total_seconds() != 0:
            raise ValueError
        return stamp.tz_convert("UTC")
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError(f"{field} must be an explicit UTC timestamp") from error


def _decimal(value, field: str, *, zero: bool = False) -> Decimal:
    try:
        number = Decimal(str(value))
        if not number.is_finite() or (number < 0 if zero else number <= 0):
            raise ValueError
        if not math.isfinite(float(number)):
            raise ValueError
        return number
    except (InvalidOperation, ValueError, TypeError, OverflowError) as error:
        raise ValueError(f"{field} must be finite and {'nonnegative' if zero else 'positive'}") from error


class HistoricalReplay:
    """Push one finalized archive row at a time into two independent cash accounts.

    Callback events and results are detached JSON values. Their hashes bind only
    information revealed at the relevant logical phase. No terminal liquidation
    or indicator evaluation on a future frame is performed.
    """

    def __init__(self, candidate: dict, registry: StrategyRegistry, *, on_event: Callable[[dict], None] | None = None):
        self._candidate = json.loads(json.dumps(candidate, allow_nan=False))
        if candidate.get("symbol") not in LOT_INCREMENTS:
            raise ValueError("historical replay supports BTCUSDT or ETHUSDT")
        if type(candidate.get("screen_passed")) is not bool:
            raise ValueError("candidate must retain its screen_passed flag")
        for key in ("stop_atr", "target_atr"):
            _decimal(candidate.get(key), key)
        if type(candidate.get("maximum_bars")) is not int or candidate["maximum_bars"] < 1:
            raise ValueError("maximum_bars must be a positive number of hours")
        strategy = registry.resolve(candidate["strategy_id"])
        if not strategy.spec.enabled or strategy.spec.definition_hash != candidate.get("strategy_definition_hash"):
            raise ValueError("candidate strategy definition does not match the enabled registry")
        self._registry = registry
        self._on_event = on_event
        self._window: deque[dict] = deque(maxlen=1000)
        self._hashes: deque[str] = deque(maxlen=1000)
        self._last_close: pd.Timestamp | None = None
        self._bars_seen = self._decision_count = self._sequence = 0
        self._journal_head: str | None = None
        self._accounts = [ReplayAccount(candidate["symbol"], multiplier, self._emit) for multiplier in (1, 2)]

    def _emit(self, kind: str, at: str, payload: dict):
        event = json_values(
            {
                "sequence": self._sequence,
                "type": kind,
                "at": at,
                "payload": payload,
                "previous_hash": self._journal_head,
            }
        )
        event["hash"] = canonical_hash(event)
        self._journal_head = event["hash"]
        self._sequence += 1
        if self._on_event is not None:
            self._on_event(event)

    def _validate(self, bar: dict) -> dict:
        for key, expected in (
            ("provider", "binance"),
            ("feed", "spot"),
            ("symbol", self._candidate["symbol"]),
            ("interval", "1h"),
        ):
            if bar.get(key) != expected:
                raise ValueError(f"bar {key} must be {expected}")
        if bar.get("finalized") is not True or type(bar.get("revision")) is not int or bar["revision"] != 1:
            raise ValueError("only finalized initial archive revision 1 is supported")
        opened = _utc(bar.get("open_timestamp"), "open_timestamp")
        closed = _utc(bar.get("close_timestamp"), "close_timestamp")
        available = _utc(bar.get("available_at"), "available_at")
        if opened != opened.floor("h") or closed - opened != pd.Timedelta(hours=1):
            raise ValueError("bar must cover one UTC clock hour")
        if available != closed:
            raise ValueError("historical bar availability must equal close_timestamp")
        if self._last_close is not None and opened < self._last_close:
            raise ValueError("duplicate, overlapping, revised or out-of-order bar")
        prices = {
            key: _decimal(bar.get(key), key, zero=key == "volume") for key in ("open", "high", "low", "close", "volume")
        }
        if (
            not prices["low"]
            <= min(prices["open"], prices["close"])
            <= max(prices["open"], prices["close"])
            <= prices["high"]
        ):
            raise ValueError("inconsistent OHLC bounds")
        return {
            "provider": "binance",
            "feed": "spot",
            "symbol": self._candidate["symbol"],
            "interval": "1h",
            "open_timestamp": opened.isoformat(),
            "close_timestamp": closed.isoformat(),
            "available_at": available.isoformat(),
            "revision": 1,
            "finalized": True,
            **{key: str(value) for key, value in prices.items()},
        }

    def push_bar(self, bar: dict) -> None:
        # Validate the envelope before accepting it; none of the later fields are
        # passed to opening execution or bound by opening / entry evidence.
        normalized = self._validate(bar)
        with localcontext(Context(prec=28)):
            self._push_validated(normalized)

    def _push_validated(self, bar: dict):
        opening = {
            key: bar[key]
            for key in ("provider", "feed", "symbol", "interval", "open_timestamp", "close_timestamp", "open")
        }
        opened = pd.Timestamp(bar["open_timestamp"])
        gap = self._last_close is not None and opened > self._last_close
        self._emit("open", bar["open_timestamp"], opening)
        if gap:
            self._emit(
                "gap",
                bar["open_timestamp"],
                {"missing_start": self._last_close.isoformat(), "missing_end": opened.isoformat()},
            )
            for account in self._accounts:
                account.gap(opening)
        for account in self._accounts:
            account.open(opening)

        self._emit("close", bar["close_timestamp"], bar)
        for account in self._accounts:
            account.close(bar, gap=gap)
        self._window.append(bar)
        self._hashes.append(canonical_hash(bar))
        self._last_close = pd.Timestamp(bar["close_timestamp"])
        self._bars_seen += 1
        decision = self._decision()
        self._decision_count += 1
        self._emit("decision", decision["at"], decision)
        for account in self._accounts:
            account.decide(decision)
            account.mark(bar)

    def _decision(self) -> dict:
        frame = pd.DataFrame(self._window)
        for key in ("open", "high", "low", "close", "volume"):
            # Python's round-trip conversion preserves the archive parser's
            # binary floats; pandas' string parser can shift their last bit.
            frame[key] = frame[key].map(float)
        for key in ("open_timestamp", "close_timestamp", "available_at"):
            frame[key] = pd.to_datetime(frame[key], utc=True)
        signal = eligible_long_signals(frame, self._candidate, self._registry).iloc[-1]
        atr = gap_safe_atr(frame, period=14).iloc[-1]
        atr_value = Decimal(str(atr)) if math.isfinite(atr) and atr > 0 else None
        at = self._last_close.isoformat()
        if _utc(signal["decision_timestamp"], "decision_timestamp").isoformat() != at:
            raise ValueError("strategy decision timestamp does not match revealed close")
        if _utc(signal["data_through"], "data_through") > self._last_close:
            raise ValueError("strategy references unavailable data")
        fingerprint = {
            "bars": len(frame),
            "first_open_timestamp": self._window[0]["open_timestamp"],
            "last_close_timestamp": at,
            "bar_hashes_hash": canonical_hash(list(self._hashes)),
        }
        decision = json_values(
            {
                "at": at,
                "candidate": self._candidate,
                "reference_close": self._window[-1]["close"],
                "atr": atr_value,
                "signal": int(signal["signal"]),
                "strength": float(signal["strength"]),
                "eligible": int(signal["signal"]) == 1 and atr_value is not None,
                "reason": str(signal["reason"]),
                "input_window": fingerprint,
                "data_through": _utc(signal["data_through"], "data_through").isoformat(),
                "stop_distance": None if atr_value is None else atr_value * Decimal(str(self._candidate["stop_atr"])),
                "target_distance": None
                if atr_value is None
                else atr_value * Decimal(str(self._candidate["target_atr"])),
                "expires_at": (self._last_close + pd.Timedelta(hours=self._candidate["maximum_bars"])).isoformat(),
            }
        )
        decision["decision_id"] = canonical_hash(decision)
        return decision

    def result(self) -> dict:
        with localcontext(Context(prec=28)):
            return json_values(
                {
                    "candidate": self._candidate,
                    "bars_seen": self._bars_seen,
                    "decision_count": self._decision_count,
                    "journal_head": self._journal_head,
                    "last_at": None if self._last_close is None else self._last_close.isoformat(),
                    "scenarios": [account.result() for account in self._accounts],
                }
            )
