from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, TypeAlias

import numpy as np
import pandas as pd

from src.strategies.indicators import (
    adx,
    atr,
    bollinger_bands,
    donchian_channels,
    ema,
    keltner_channels,
    macd,
    relative_volume,
    rolling_zscore,
    rsi,
    session_vwap,
    stochastic,
)
from src.strategies.pairs import aligned_peer_close, rolling_cointegration_zscore
from src.strategies.registry import StrategyMetadata, StrategyRegistry
from src.strategies.session import SessionCalendar
from src.strategies.types import ParameterValue, StrategySpec

StrategySignalFrame = pd.DataFrame


@dataclass(frozen=True, slots=True)
class StrategyContext:
    session: SessionCalendar = field(default_factory=SessionCalendar.continuous_utc)
    paired_bars: pd.DataFrame | None = None
    universe_bars: Mapping[str, pd.DataFrame] = field(default_factory=lambda: MappingProxyType({}))
    universe_membership: pd.DataFrame | None = None


@dataclass(frozen=True, slots=True)
class _RuleResult:
    signal: pd.Series
    strength: pd.Series | None = None
    reason: pd.Series | None = None
    valid: pd.Series | None = None


@dataclass(frozen=True, slots=True)
class PrefixAuditResult:
    passed: bool
    mismatch_row: int | None = None
    mismatch_column: str | None = None
    reason: str = "prefix signals are invariant"


Rule: TypeAlias = Callable[[pd.DataFrame, Mapping[str, ParameterValue], StrategyContext], _RuleResult]
RegisteredGenerator: TypeAlias = Callable[[StrategySpec, pd.DataFrame, StrategyContext], StrategySignalFrame]


def _series(values: Sequence[float] | pd.Series, index: pd.Index) -> pd.Series:
    if isinstance(values, pd.Series):
        return values.astype(float).set_axis(index)
    return pd.Series(values, index=index, dtype=float)


def _empty_signal(bars: pd.DataFrame) -> pd.Series:
    return pd.Series(0, index=bars.index, dtype="int8")


def _direction(long: pd.Series, short: pd.Series) -> pd.Series:
    result = pd.Series(0, index=long.index, dtype="int8")
    result.loc[long.fillna(False)] = 1
    result.loc[short.fillna(False)] = -1
    return result


def _parameter(parameters: Mapping[str, ParameterValue], name: str, cast: Callable[[Any], Any]) -> Any:
    try:
        return cast(parameters[name])
    except KeyError as error:
        raise ValueError(f"strategy parameter '{name}' is required") from error


def _ema_adx(bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], _: StrategyContext) -> _RuleResult:
    fast = ema(bars["close"], _parameter(parameters, "fast_period", int))
    slow = ema(bars["close"], _parameter(parameters, "slow_period", int))
    trend_strength = adx(
        bars["high"], bars["low"], bars["close"], _parameter(parameters, "adx_period", int)
    )
    threshold = _parameter(parameters, "adx_threshold", float)
    active = trend_strength >= threshold
    signal = _direction(active & (fast > slow), active & (fast < slow))
    valid = fast.notna() & slow.notna() & trend_strength.notna()
    return _RuleResult(signal, (trend_strength / 100).clip(0, 1), valid=valid)


def _macd_histogram(
    bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], _: StrategyContext
) -> _RuleResult:
    _, _, histogram = macd(
        bars["close"],
        _parameter(parameters, "fast_period", int),
        _parameter(parameters, "slow_period", int),
        _parameter(parameters, "signal_period", int),
    )
    scale = histogram.abs().rolling(_parameter(parameters, "signal_period", int), min_periods=1).max()
    return _RuleResult(
        _direction(histogram > 0, histogram < 0),
        (histogram.abs() / scale).clip(0, 1),
        valid=histogram.notna(),
    )


def _donchian(bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], _: StrategyContext) -> _RuleResult:
    upper, lower = donchian_channels(bars["high"], bars["low"], _parameter(parameters, "lookback", int))
    signal = _direction(bars["close"] > upper, bars["close"] < lower)
    width = (upper - lower).replace(0, np.nan)
    strength = ((bars["close"] - upper).clip(lower=0) + (lower - bars["close"]).clip(lower=0)) / width
    return _RuleResult(signal, strength.clip(0, 1), valid=upper.notna() & lower.notna())


def _supertrend(bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], _: StrategyContext) -> _RuleResult:
    period = _parameter(parameters, "atr_period", int)
    multiplier = _parameter(parameters, "multiplier", float)
    average_range = atr(bars["high"], bars["low"], bars["close"], period)
    midpoint = (bars["high"].astype(float) + bars["low"].astype(float)) / 2
    upper = midpoint + multiplier * average_range
    lower = midpoint - multiplier * average_range
    signal = _empty_signal(bars)
    direction = 0
    for position in range(1, len(bars)):
        previous_upper = upper.iloc[position - 1]
        previous_lower = lower.iloc[position - 1]
        close = float(bars["close"].iloc[position])
        if pd.isna(previous_upper) or pd.isna(previous_lower):
            continue
        if close > previous_upper:
            direction = 1
        elif close < previous_lower:
            direction = -1
        signal.iloc[position] = direction
    strength = (bars["close"] - midpoint).abs() / (multiplier * average_range).replace(0, np.nan)
    valid = upper.shift(1).notna() & lower.shift(1).notna()
    return _RuleResult(signal, strength.clip(0, 1), valid=valid)


def _vwap_trend(bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], context: StrategyContext) -> _RuleResult:
    timestamps = _bar_timestamps(bars, "open_timestamp")
    vwap = session_vwap(
        bars["high"], bars["low"], bars["close"], bars["volume"], timestamps, context.session
    )
    slope_bars = _parameter(parameters, "slope_bars", int)
    slope = vwap.diff(slope_bars)
    signal = _direction((bars["close"] > vwap) & (slope > 0), (bars["close"] < vwap) & (slope < 0))
    strength = (bars["close"] - vwap).abs() / vwap.abs().replace(0, np.nan)
    valid = vwap.notna() & slope.notna() & context.session.in_session(timestamps)
    return _RuleResult(signal, strength.clip(0, 1), valid=valid)


def _rsi_reversal(bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], _: StrategyContext) -> _RuleResult:
    value = rsi(bars["close"], _parameter(parameters, "period", int))
    oversold = _parameter(parameters, "oversold", float)
    overbought = _parameter(parameters, "overbought", float)
    strength = ((50 - value).abs() / 50).clip(0, 1)
    return _RuleResult(_direction(value <= oversold, value >= overbought), strength, valid=value.notna())


def _streak(close: pd.Series) -> pd.Series:
    result = pd.Series(0.0, index=close.index)
    changes = close.astype(float).diff()
    for position in range(1, len(close)):
        change = changes.iloc[position]
        previous = result.iloc[position - 1]
        if change > 0:
            result.iloc[position] = previous + 1 if previous > 0 else 1
        elif change < 0:
            result.iloc[position] = previous - 1 if previous < 0 else -1
    return result


def _percent_rank(values: pd.Series, period: int) -> pd.Series:
    return values.rolling(period, min_periods=period).apply(
        lambda window: float(np.count_nonzero(window <= window[-1]) / len(window) * 100), raw=True
    )


def _connors_rsi(bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], _: StrategyContext) -> _RuleResult:
    close_rsi = rsi(bars["close"], _parameter(parameters, "rsi_period", int))
    streak_rsi = rsi(_streak(bars["close"]), _parameter(parameters, "streak_period", int))
    rank = _percent_rank(bars["close"].pct_change(), _parameter(parameters, "rank_period", int))
    connors = (close_rsi + streak_rsi + rank) / 3
    return _RuleResult(
        _direction(connors <= 20, connors >= 80),
        ((connors - 50).abs() / 50).clip(0, 1),
        valid=connors.notna(),
    )


def _bollinger_reversion(
    bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], _: StrategyContext
) -> _RuleResult:
    middle, upper, lower = bollinger_bands(
        bars["close"], _parameter(parameters, "period", int), _parameter(parameters, "deviations", float)
    )
    signal = _direction(bars["close"] < lower, bars["close"] > upper)
    strength = (bars["close"] - middle).abs() / (upper - middle).replace(0, np.nan)
    valid = middle.notna() & upper.notna() & lower.notna()
    return _RuleResult(signal, strength.clip(0, 1), valid=valid)


def _vwap_zscore(bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], context: StrategyContext) -> _RuleResult:
    timestamps = _bar_timestamps(bars, "open_timestamp")
    vwap = session_vwap(
        bars["high"], bars["low"], bars["close"], bars["volume"], timestamps, context.session
    )
    score = rolling_zscore(bars["close"] - vwap, _parameter(parameters, "lookback", int))
    entry = _parameter(parameters, "entry_zscore", float)
    valid = score.notna() & vwap.notna() & context.session.in_session(timestamps)
    return _RuleResult(
        _direction(score <= -entry, score >= entry),
        (score.abs() / entry).clip(0, 1),
        valid=valid,
    )


def _stochastic_reversal(
    bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], _: StrategyContext
) -> _RuleResult:
    percent_k, _ = stochastic(
        bars["high"],
        bars["low"],
        bars["close"],
        _parameter(parameters, "k_period", int),
        _parameter(parameters, "d_period", int),
    )
    return _RuleResult(
        _direction(percent_k <= 20, percent_k >= 80),
        ((percent_k - 50).abs() / 50).clip(0, 1),
        valid=percent_k.notna(),
    )


def _extreme_return(bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], _: StrategyContext) -> _RuleResult:
    score = rolling_zscore(bars["close"].pct_change(), _parameter(parameters, "lookback", int))
    entry = _parameter(parameters, "entry_zscore", float)
    return _RuleResult(
        _direction(score <= -entry, score >= entry),
        (score.abs() / entry).clip(0, 1),
        valid=score.notna(),
    )


def _squeeze(bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], _: StrategyContext) -> _RuleResult:
    period = _parameter(parameters, "period", int)
    _, bollinger_upper, bollinger_lower = bollinger_bands(bars["close"], period)
    _, keltner_upper, keltner_lower = keltner_channels(
        bars["high"], bars["low"], bars["close"], period, _parameter(parameters, "atr_period", int), 1.5
    )
    squeeze = (bollinger_upper < keltner_upper) & (bollinger_lower > keltner_lower)
    released = squeeze.shift(1, fill_value=False)
    signal = _direction(
        released & (bars["close"] > bollinger_upper.shift(1)),
        released & (bars["close"] < bollinger_lower.shift(1)),
    )
    strength = (bars["close"] - bollinger_upper.shift(1)).abs() / (
        bollinger_upper.shift(1) - bollinger_lower.shift(1)
    ).replace(0, np.nan)
    valid = (
        bollinger_upper.shift(1).notna()
        & bollinger_lower.shift(1).notna()
        & keltner_upper.shift(1).notna()
        & keltner_lower.shift(1).notna()
    )
    return _RuleResult(signal, strength.clip(0, 1), valid=valid)


def _volume_breakout(
    bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], _: StrategyContext
) -> _RuleResult:
    lookback = _parameter(parameters, "volume_lookback", int)
    multiple = _parameter(parameters, "volume_multiple", float)
    relative = relative_volume(bars["volume"], lookback)
    upper, lower = donchian_channels(bars["high"], bars["low"], lookback)
    active = relative >= multiple
    signal = _direction(active & (bars["close"] > upper), active & (bars["close"] < lower))
    valid = relative.notna() & upper.notna() & lower.notna()
    return _RuleResult(signal, (relative / multiple).clip(0, 1), valid=valid)


def _volatility_scaled_trend(
    bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], _: StrategyContext
) -> _RuleResult:
    trend = bars["close"].pct_change(_parameter(parameters, "trend_lookback", int))
    volatility = bars["close"].pct_change().rolling(
        _parameter(parameters, "volatility_lookback", int),
        min_periods=_parameter(parameters, "volatility_lookback", int),
    ).std(ddof=0)
    scaled = trend / volatility.replace(0, np.nan)
    return _RuleResult(
        _direction(scaled > 0, scaled < 0),
        scaled.abs().clip(0, 1),
        valid=scaled.notna() & np.isfinite(scaled),
    )


def _opening_range(
    bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], context: StrategyContext
) -> _RuleResult:
    timestamps = _bar_timestamps(bars, "open_timestamp")
    labels = context.session.session_labels(timestamps)
    in_range = context.session.opening_range(timestamps, _parameter(parameters, "range_minutes", int))
    range_high = bars["high"].where(in_range).groupby(labels, sort=False).cummax().groupby(labels, sort=False).ffill()
    range_low = bars["low"].where(in_range).groupby(labels, sort=False).cummin().groupby(labels, sort=False).ffill()
    relative = relative_volume(bars["volume"], _parameter(parameters, "relative_volume_lookback", int))
    after_range = ~in_range
    active = after_range & (relative > 1)
    signal = _direction(active & (bars["close"] > range_high), active & (bars["close"] < range_low))
    valid = (
        context.session.in_session(timestamps)
        & after_range
        & relative.notna()
        & range_high.notna()
        & range_low.notna()
    )
    return _RuleResult(signal, relative.clip(0, 1), valid=valid)


def _last_half_hour(
    bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], context: StrategyContext
) -> _RuleResult:
    timestamps = _bar_timestamps(bars, "open_timestamp")
    trend = bars["close"].pct_change(_parameter(parameters, "lookback", int))
    active = context.session.last_window(timestamps, 30)
    return _RuleResult(
        _direction(active & (trend > 0), active & (trend < 0)),
        trend.abs().clip(0, 1),
        valid=trend.notna(),
    )


def _bitcoin_active(
    bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], context: StrategyContext
) -> _RuleResult:
    timestamps = _bar_timestamps(bars, "open_timestamp")
    trend = bars["close"].pct_change(_parameter(parameters, "lookback", int))
    active = context.session.active_window(timestamps)
    return _RuleResult(
        _direction(active & (trend > 0), active & (trend < 0)),
        trend.abs().clip(0, 1),
        valid=trend.notna(),
    )


def _pairs(bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], context: StrategyContext) -> _RuleResult:
    if context.paired_bars is None or context.paired_bars.empty:
        reason = pd.Series(
            "abstain: paired instrument point-in-time data is unavailable",
            index=bars.index,
            dtype="object",
        )
        return _RuleResult(_empty_signal(bars), reason=reason, valid=pd.Series(False, index=bars.index))
    peer = aligned_peer_close(bars, context.paired_bars)
    score = rolling_cointegration_zscore(
        bars["close"].astype(float), peer, _parameter(parameters, "lookback", int)
    )
    entry = _parameter(parameters, "entry_zscore", float)
    return _RuleResult(
        _direction(score <= -entry, score >= entry),
        (score.abs() / entry).clip(0, 1),
        valid=score.notna(),
    )


def _cross_sectional(
    bars: pd.DataFrame, parameters: Mapping[str, ParameterValue], context: StrategyContext
) -> _RuleResult:
    lookback = _parameter(parameters, "lookback", int)
    minimum = _parameter(parameters, "minimum_universe", int)
    symbol = str(bars["symbol"].iloc[0]) if "symbol" in bars and not bars.empty else ""
    if context.universe_membership is None or context.universe_membership.empty:
        reasons = pd.Series(
            "abstain: point-in-time universe membership is unavailable",
            index=bars.index,
            dtype="object",
        )
        return _RuleResult(
            _empty_signal(bars), reason=reasons, valid=pd.Series(False, index=bars.index)
        )
    eligible_symbols = set(
        context.universe_membership.loc[
            context.universe_membership["member"] & context.universe_membership["liquid"],
            "symbol",
        ].astype(str)
    )
    universe = {
        member: member_bars
        for member, member_bars in context.universe_bars.items()
        if member in eligible_symbols
    }
    if symbol not in eligible_symbols:
        reasons = pd.Series(
            "abstain: focal instrument is not in the point-in-time eligible universe",
            index=bars.index,
            dtype="object",
        )
        return _RuleResult(
            _empty_signal(bars), reason=reasons, valid=pd.Series(False, index=bars.index)
        )
    universe.setdefault(symbol, bars)
    main_times = pd.to_datetime(bars[_timestamp_name(bars)], utc=True)
    returns_by_symbol: dict[str, pd.Series] = {}
    for member, member_bars in universe.items():
        member_times = pd.to_datetime(member_bars[_timestamp_name(member_bars)], utc=True)
        if not member_times.is_monotonic_increasing or member_times.duplicated().any():
            raise ValueError("cross-sectional universe bars must be ordered and unique")
        values = member_bars["close"].astype(float).pct_change(lookback)
        returns_by_symbol[member] = pd.Series(values.to_numpy(), index=member_times)

    signal = _empty_signal(bars)
    strength = pd.Series(0.0, index=bars.index)
    valid = pd.Series(False, index=bars.index)
    reasons = pd.Series("abstain: indicator warm-up incomplete", index=bars.index, dtype="object")
    for position, timestamp in enumerate(main_times):
        observations = {
            member: float(series.loc[timestamp])
            for member, series in returns_by_symbol.items()
            if timestamp in series.index and pd.notna(series.loc[timestamp])
        }
        count = len(observations)
        if count < minimum or symbol not in observations:
            reasons.iloc[position] = (
                f"abstain: cross-sectional universe has {count} of {minimum} required instruments"
            )
            continue
        ranked = pd.Series(observations).rank(method="average", pct=True)
        valid.iloc[position] = True
        percentile = float(ranked[symbol])
        if percentile >= 0.8:
            signal.iloc[position] = 1
            reasons.iloc[position] = "long: return ranks in the trailing top quintile"
        elif percentile <= 0.2:
            signal.iloc[position] = -1
            reasons.iloc[position] = "short: return ranks in the trailing bottom quintile"
        else:
            reasons.iloc[position] = "abstain: return rank is between the entry quintiles"
        strength.iloc[position] = min(1.0, abs(percentile - 0.5) * 2)
    return _RuleResult(signal, strength, reasons, valid)


_RULES: Mapping[str, Rule] = MappingProxyType(
    {
        "ema_adx_trend": _ema_adx,
        "macd_histogram_trend": _macd_histogram,
        "donchian_breakout": _donchian,
        "supertrend": _supertrend,
        "vwap_trend_continuation": _vwap_trend,
        "rsi_reversal": _rsi_reversal,
        "connors_rsi": _connors_rsi,
        "bollinger_reversion": _bollinger_reversion,
        "vwap_zscore_reversion": _vwap_zscore,
        "stochastic_reversal": _stochastic_reversal,
        "extreme_return_reversal": _extreme_return,
        "bollinger_keltner_squeeze": _squeeze,
        "volume_spike_breakout": _volume_breakout,
        "volatility_scaled_trend": _volatility_scaled_trend,
        "opening_range_breakout": _opening_range,
        "etf_last_half_hour_momentum": _last_half_hour,
        "bitcoin_active_session_momentum": _bitcoin_active,
        "rolling_cointegration_pairs": _pairs,
        "crypto_cross_sectional_momentum": _cross_sectional,
    }
)


def _timestamp_name(bars: pd.DataFrame) -> str:
    for name in ("close_timestamp", "open_timestamp", "timestamp"):
        if name in bars:
            return name
    raise ValueError("bars require a timestamp column")


def _bar_timestamps(bars: pd.DataFrame, preferred: str) -> pd.Series:
    name = preferred if preferred in bars else _timestamp_name(bars)
    return pd.Series(pd.to_datetime(bars[name], utc=True), index=bars.index)


_BAR_IDENTITY = ["provider", "feed", "symbol", "interval", "open_timestamp"]


def _validate_revision_ledger(bars: pd.DataFrame, *, label: str = "primary") -> pd.DataFrame:
    required = {
        *_BAR_IDENTITY,
        "close_timestamp",
        "available_at",
        "revision",
        "finalized",
        "high",
        "low",
        "close",
        "volume",
    }
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(
            f"{label} bars must be a revision ledger; missing columns: {', '.join(sorted(missing))}"
        )
    ledger = bars.reset_index(drop=True).copy()
    for column in ("open_timestamp", "close_timestamp", "available_at"):
        ledger[column] = pd.to_datetime(ledger[column], utc=True)
    if not ledger["finalized"].map(lambda value: value is True or value == 1).all():
        raise ValueError(f"{label} revision ledger requires finalized bars")
    if (ledger["revision"].astype(int) <= 0).any():
        raise ValueError(f"{label} revision ledger requires positive revisions")
    if (ledger["open_timestamp"] >= ledger["close_timestamp"]).any():
        raise ValueError(f"{label} revision ledger has invalid bar boundaries")
    if (ledger["available_at"] < ledger["close_timestamp"]).any():
        raise ValueError(f"{label} revision ledger contains a bar available before its close")
    revision_key = [*_BAR_IDENTITY, "revision"]
    if ledger.duplicated(revision_key).any():
        raise ValueError(f"{label} revision ledger contains duplicate revisions")
    instrument_key = ["provider", "feed", "symbol", "interval"]
    if len(ledger[instrument_key].drop_duplicates()) > 1:
        raise ValueError(f"{label} revision ledger must contain exactly one instrument and feed")
    return ledger


def _snapshot_as_of(ledger: pd.DataFrame, decision_timestamp: pd.Timestamp) -> pd.DataFrame:
    eligible = ledger[
        (ledger["finalized"])
        & (ledger["available_at"] <= decision_timestamp)
        & (ledger["close_timestamp"] <= decision_timestamp)
    ]
    return (
        eligible.sort_values(["open_timestamp", "available_at", "revision"])
        .drop_duplicates(_BAR_IDENTITY, keep="last")
        .sort_values("open_timestamp")
        .reset_index(drop=True)
    )


def _decision_events(ledger: pd.DataFrame) -> pd.DataFrame:
    return (
        ledger.sort_values(["open_timestamp", "available_at", "revision"])
        .drop_duplicates(_BAR_IDENTITY, keep="first")
        .sort_values("open_timestamp")
        .reset_index(drop=True)
    )


def _is_monotonic_unique_snapshot(ledger: pd.DataFrame) -> bool:
    events = _decision_events(ledger)
    return len(events) == len(ledger) and events["available_at"].is_monotonic_increasing


def _membership_as_of(membership: pd.DataFrame | None, decision_timestamp: pd.Timestamp) -> pd.DataFrame | None:
    if membership is None:
        return None
    required = {
        "symbol",
        "effective_from",
        "effective_to",
        "available_at",
        "member",
        "liquid",
    }
    if required - set(membership.columns):
        return None
    records = membership.copy()
    records["effective_from"] = pd.to_datetime(records["effective_from"], utc=True)
    records["effective_to"] = pd.to_datetime(records["effective_to"], utc=True)
    records["available_at"] = pd.to_datetime(records["available_at"], utc=True)
    active = records[
        (records["available_at"] <= decision_timestamp)
        & (records["effective_from"] <= decision_timestamp)
        & (records["effective_to"].isna() | (decision_timestamp < records["effective_to"]))
    ]
    return (
        active.sort_values(["symbol", "available_at", "effective_from"])
        .drop_duplicates("symbol", keep="last")
        .reset_index(drop=True)
    )


def _context_as_of(context: StrategyContext, decision_timestamp: pd.Timestamp) -> StrategyContext:
    paired: pd.DataFrame | None = None
    if context.paired_bars is not None:
        try:
            paired = _snapshot_as_of(
                _validate_revision_ledger(context.paired_bars, label="paired instrument"),
                decision_timestamp,
            )
        except (KeyError, TypeError, ValueError):
            paired = None

    universe: dict[str, pd.DataFrame] = {}
    for symbol, member_bars in context.universe_bars.items():
        try:
            universe[symbol] = _snapshot_as_of(
                _validate_revision_ledger(member_bars, label=f"universe member {symbol}"),
                decision_timestamp,
            )
        except (KeyError, TypeError, ValueError):
            continue
    return StrategyContext(
        session=context.session,
        paired_bars=paired,
        universe_bars=universe,
        universe_membership=_membership_as_of(context.universe_membership, decision_timestamp),
    )


def _result_frame(spec: StrategySpec, bars: pd.DataFrame, result: _RuleResult) -> StrategySignalFrame:
    close_name = "close_timestamp" if "close_timestamp" in bars else _timestamp_name(bars)
    data_through = pd.to_datetime(bars[close_name], utc=True)
    decision_name = "available_at" if "available_at" in bars else close_name
    decisions = pd.to_datetime(bars[decision_name], utc=True)
    if (decisions < data_through).any():
        raise ValueError("a decision cannot precede the finalized data it uses")

    signal = result.signal.reindex(bars.index).fillna(0).astype("int8")
    if not signal.isin((-1, 0, 1)).all():
        raise ValueError("strategy signals must be -1, 0, or 1")
    warmup_ready = pd.Series(np.arange(len(bars)) + 1 >= spec.warmup_bars, index=bars.index)
    valid = (
        result.valid.reindex(bars.index).fillna(False).astype(bool)
        if result.valid is not None
        else pd.Series(False, index=bars.index)
    )
    ready = warmup_ready & valid
    signal = signal.where(ready, 0).astype("int8")
    if result.strength is None:
        strength = signal.abs().astype(float)
    else:
        strength = result.strength.reindex(bars.index).fillna(0).clip(0, 1).astype(float)
        strength = strength.where(ready, 0.0).where(signal != 0, 0.0)

    if result.reason is None:
        reason = pd.Series("abstain: no rule condition met", index=bars.index, dtype="object")
        reason.loc[signal > 0] = "long: configured trailing rule condition met"
        reason.loc[signal < 0] = "short: configured trailing rule condition met"
    else:
        reason = result.reason.reindex(bars.index).fillna("abstain: no rule condition met").astype(str)
    if result.reason is None:
        reason.loc[warmup_ready & ~valid] = "abstain: rule inputs are not yet valid"
    reason.loc[~warmup_ready] = f"abstain: strategy requires {spec.warmup_bars} warm-up bars"

    return pd.DataFrame(
        {
            "decision_timestamp": decisions,
            "data_through": data_through,
            "signal": signal,
            "strength": strength,
            "reason": reason,
        }
    )


def _generate_for_rule(
    strategy_id: str, spec: StrategySpec, bars: pd.DataFrame, context: StrategyContext
) -> StrategySignalFrame:
    if spec.strategy_id != strategy_id:
        raise ValueError(f"generator for '{strategy_id}' cannot run strategy '{spec.strategy_id}'")
    ledger = _validate_revision_ledger(bars)
    if strategy_id not in {"rolling_cointegration_pairs", "crypto_cross_sectional_momentum"} and (
        _is_monotonic_unique_snapshot(ledger)
    ):
        snapshot = ledger.sort_values("open_timestamp").reset_index(drop=True)
        return _result_frame(spec, snapshot, _RULES[strategy_id](snapshot, spec.parameters, context))
    rows: list[pd.Series] = []
    for event in _decision_events(ledger).itertuples(index=False):
        decision = pd.Timestamp(event.available_at)
        snapshot = _snapshot_as_of(ledger, decision)
        point_in_time_context = _context_as_of(context, decision)
        result = _result_frame(
            spec,
            snapshot,
            _RULES[strategy_id](snapshot, spec.parameters, point_in_time_context),
        )
        target = result.index[snapshot["open_timestamp"] == pd.Timestamp(event.open_timestamp)]
        if len(target) != 1:
            raise ValueError("revision ledger could not resolve the decision bar in its point-in-time snapshot")
        row = result.loc[target[0]].copy()
        row["decision_timestamp"] = decision
        row["data_through"] = pd.Timestamp(event.close_timestamp)
        rows.append(row)
    return pd.DataFrame(rows, columns=["decision_timestamp", "data_through", "signal", "strength", "reason"])


def _generator(strategy_id: str) -> RegisteredGenerator:
    def run(spec: StrategySpec, bars: pd.DataFrame, context: StrategyContext) -> StrategySignalFrame:
        return _generate_for_rule(strategy_id, spec, bars, context)

    run.__name__ = f"generate_{strategy_id}"
    return run


STRATEGY_GENERATORS: Mapping[str, RegisteredGenerator] = MappingProxyType(
    {strategy_id: _generator(strategy_id) for strategy_id in _RULES}
)


def _metadata(
    description: str, evidence_strength: str = "heuristic", *, literature: str | None = None
) -> StrategyMetadata:
    evidence_note = (
        f"Research prior from {literature}; requires causal walk-forward and sealed final backtest promotion."
        if literature
        else "Canonical technical heuristic; requires causal walk-forward and sealed final backtest promotion."
    )
    return StrategyMetadata(
        description=description,
        evidence_strength=evidence_strength,
        evidence_note=evidence_note,
        research_only=True,
    )


STRATEGY_METADATA: Mapping[str, StrategyMetadata] = MappingProxyType(
    {
        "ema_adx_trend": _metadata("Follows the EMA direction only when trailing ADX clears its threshold."),
        "macd_histogram_trend": _metadata("Follows the sign of the trailing MACD histogram."),
        "donchian_breakout": _metadata("Follows a close beyond the prior trailing Donchian range."),
        "supertrend": _metadata("Follows closes that cross trailing ATR bands and holds the detected direction."),
        "vwap_trend_continuation": _metadata(
            "Follows price above a rising session VWAP or below a falling session VWAP."
        ),
        "rsi_reversal": _metadata("Fades trailing Wilder RSI readings beyond configured extremes."),
        "connors_rsi": _metadata("Fades extremes in trailing price RSI, streak RSI, and return percentile rank."),
        "bollinger_reversion": _metadata(
            "Fades closes outside trailing population-standard-deviation Bollinger bands."
        ),
        "vwap_zscore_reversion": _metadata("Fades extreme trailing z-scores of price distance from session VWAP."),
        "stochastic_reversal": _metadata("Fades trailing stochastic readings in the lower or upper extreme."),
        "extreme_return_reversal": _metadata("Fades return shocks beyond a configured trailing z-score."),
        "bollinger_keltner_squeeze": _metadata(
            "Follows a price break after trailing Bollinger bands contract inside Keltner channels."
        ),
        "volume_spike_breakout": _metadata(
            "Follows a prior-range break when current volume exceeds its trailing baseline."
        ),
        "volatility_scaled_trend": _metadata(
            "Follows trailing time-series momentum and scales strength by trailing volatility.",
            "research_prior",
            literature="Moskowitz, Ooi, and Pedersen time-series momentum",
        ),
        "opening_range_breakout": _metadata(
            "Follows an equity opening-range break only with above-baseline trailing volume."
        ),
        "etf_last_half_hour_momentum": _metadata(
            "Follows trailing ETF momentum only during the final half hour of the equity session.",
            "research_prior",
            literature="Gao et al. market intraday momentum",
        ),
        "bitcoin_active_session_momentum": _metadata(
            "Follows trailing Bitcoin momentum only during a fixed active UTC window."
        ),
        "rolling_cointegration_pairs": _metadata(
            "Fades the trailing standardized residual of a rolling log-price pair regression.",
            "research_prior",
            literature="Gatev, Goetzmann, and Rouwenhorst pairs trading",
        ),
        "crypto_cross_sectional_momentum": _metadata(
            "Ranks a point-in-time liquid-crypto universe by trailing return and selects only extreme quintiles.",
            "research_prior",
            literature="cross-sectional momentum research",
        ),
    }
)


def generate_signals(
    spec: StrategySpec, bars: pd.DataFrame, context: StrategyContext
) -> StrategySignalFrame:
    try:
        generator = STRATEGY_GENERATORS[spec.strategy_id]
    except KeyError as error:
        raise KeyError(f"Unknown strategy '{spec.strategy_id}'") from error
    return generator(spec, bars, context)


def audit_prefix_invariance(
    spec: StrategySpec,
    prefix_bars: pd.DataFrame,
    extended_bars: pd.DataFrame,
    prefix_context: StrategyContext,
    extended_context: StrategyContext,
    *,
    generator: RegisteredGenerator | None = None,
) -> PrefixAuditResult:
    """Compare every emitted prefix field before and after future data is appended."""

    if len(extended_bars) < len(prefix_bars):
        raise ValueError("extended bars cannot be shorter than the audited prefix")
    selected = generator or generate_signals
    before = selected(spec, prefix_bars, prefix_context).reset_index(drop=True)
    after = selected(spec, extended_bars, extended_context).iloc[: len(before)].reset_index(drop=True)
    if tuple(before.columns) != tuple(after.columns):
        return PrefixAuditResult(False, reason="signal frame columns changed after future data was appended")
    for column in before.columns:
        for row, (left, right) in enumerate(zip(before[column], after[column], strict=True)):
            if pd.isna(left) and pd.isna(right):
                continue
            if column == "strength":
                equal = bool(np.isclose(float(left), float(right), rtol=0, atol=1e-12))
            else:
                equal = bool(left == right)
            if not equal:
                return PrefixAuditResult(
                    False,
                    mismatch_row=row,
                    mismatch_column=column,
                    reason=f"row {row} column '{column}' changed after future data was appended",
                )
    return PrefixAuditResult(True)


def build_strategy_registry(specs: Sequence[StrategySpec]) -> StrategyRegistry:
    registry = StrategyRegistry()
    registry.register_configured(specs, STRATEGY_GENERATORS, STRATEGY_METADATA)
    return registry


__all__ = [
    "PrefixAuditResult",
    "STRATEGY_GENERATORS",
    "STRATEGY_METADATA",
    "StrategyContext",
    "StrategySignalFrame",
    "audit_prefix_invariance",
    "build_strategy_registry",
    "generate_signals",
]
