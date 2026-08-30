"""Causal market-regime features and conservative soft posteriors."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

from src.contextual.types import MarketRegime
from src.strategies.types import canonical_hash

REGIME_FEATURE_COLUMNS = (
    "trend_slope_short",
    "trend_slope_medium",
    "directional_consistency",
    "trend_strength",
    "realized_volatility_short",
    "realized_volatility_medium",
    "volatility_percentile",
    "volatility_of_volatility",
    "relative_volume",
    "volume_concentration",
    "relative_spread",
    "depth_imbalance",
    "market_breadth",
    "gap_return",
    "continuity",
)

_PRIOR = MappingProxyType(
    {
        MarketRegime.TREND_NORMAL: 0.15,
        MarketRegime.TREND_ELEVATED_VOLATILITY: 0.15,
        MarketRegime.RANGE_LIQUID: 0.25,
        MarketRegime.STRESSED_OR_ILLIQUID: 0.45,
    }
)


@dataclass(frozen=True, slots=True)
class RegimeFit:
    status: Literal["fitted", "parent_fallback"]
    feature_names: tuple[str, ...]
    classes: tuple[MarketRegime, ...]
    training_rows: int
    training_through: datetime | None
    thresholds: MappingProxyType
    scaler_mean: tuple[float, ...]
    scaler_scale: tuple[float, ...]
    coefficients: tuple[tuple[float, ...], ...]
    intercepts: tuple[float, ...]
    model_hash: str
    scaler: StandardScaler | None = field(default=None, repr=False, compare=False)
    classifier: LogisticRegression | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class RegimePosteriorFrame:
    probabilities: np.ndarray
    regimes: tuple[MarketRegime, ...]
    index: tuple[object, ...]
    status: Literal["fitted", "parent_fallback"]
    model_hash: str
    evidence_through: datetime | None
    posterior_hash: str

    def as_frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            self.probabilities.copy(),
            index=pd.Index(self.index),
            columns=[regime.value for regime in self.regimes],
        )


def _strict_utc_series(values: pd.Series, label: str) -> pd.Series:
    parsed = pd.to_datetime(values, errors="raise")
    timezone = getattr(parsed.dtype, "tz", None)
    if timezone is None or str(timezone) != "UTC":
        raise ValueError(f"{label} must contain explicit UTC timestamps")
    return parsed


def _rolling_slope(values: np.ndarray) -> float:
    if len(values) < 2 or not np.isfinite(values).all():
        return math.nan
    x = np.arange(len(values), dtype=float)
    x -= x.mean()
    denominator = float(x @ x)
    return float(x @ (values - values.mean()) / denominator) if denominator > 0 else 0.0


def _last_percentile(values: np.ndarray) -> float:
    finite = values[np.isfinite(values)]
    if len(finite) < 2:
        return math.nan
    return float((finite <= finite[-1]).sum() / len(finite))


def causal_regime_features(bars: pd.DataFrame) -> pd.DataFrame:
    """Return prefix-invariant features; row ``t`` uses finalized rows through ``t-1``."""

    required = {
        "open_timestamp",
        "close_timestamp",
        "available_at",
        "finalized",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }
    missing = sorted(required - set(bars.columns))
    if missing:
        raise ValueError(f"bars missing regime columns: {', '.join(missing)}")
    if bars.empty:
        raise ValueError("regime features require at least one finalized bar")

    frame = bars.copy()
    for column in ("open_timestamp", "close_timestamp", "available_at"):
        frame[column] = _strict_utc_series(frame[column], column)
    finalized = frame["finalized"].map(lambda value: value is True or value == 1)
    if not finalized.all():
        raise ValueError("regime features require finalized bars")
    if not frame["open_timestamp"].is_monotonic_increasing or frame["open_timestamp"].duplicated().any():
        raise ValueError("regime bars must be uniquely time ordered")
    if (frame["available_at"] < frame["close_timestamp"]).any():
        raise ValueError("regime bars cannot be available before close")

    numeric_columns = ["open", "high", "low", "close", "volume"]
    numeric = frame[numeric_columns].apply(pd.to_numeric, errors="coerce").astype(float)
    if (
        not np.isfinite(numeric.to_numpy()).all()
        or (numeric[["open", "high", "low", "close"]] <= 0).any().any()
        or (numeric["volume"] < 0).any()
    ):
        raise ValueError("regime OHLCV values must be finite with positive prices")

    log_close = np.log(numeric["close"])
    lagged_log_close = log_close.shift(1)
    lagged_return = log_close.diff().shift(1)
    lagged_volume = numeric["volume"].shift(1)

    result = pd.DataFrame(index=frame.index)
    result["trend_slope_short"] = lagged_log_close.rolling(12, min_periods=8).apply(_rolling_slope, raw=True)
    result["trend_slope_medium"] = lagged_log_close.rolling(48, min_periods=24).apply(_rolling_slope, raw=True)
    result["directional_consistency"] = lagged_return.rolling(24, min_periods=12).apply(
        lambda values: abs(float(np.sign(values).mean())), raw=True
    )
    signed_move = lagged_return.rolling(24, min_periods=12).sum().abs()
    total_move = lagged_return.abs().rolling(24, min_periods=12).sum()
    result["trend_strength"] = signed_move / total_move.replace(0, np.nan)
    result["realized_volatility_short"] = lagged_return.rolling(12, min_periods=8).std(ddof=1)
    result["realized_volatility_medium"] = lagged_return.rolling(48, min_periods=24).std(ddof=1)
    result["volatility_percentile"] = result["realized_volatility_short"].rolling(
        100, min_periods=20
    ).apply(_last_percentile, raw=True)
    result["volatility_of_volatility"] = result["realized_volatility_short"].rolling(
        24, min_periods=12
    ).std(ddof=1)
    volume_median = lagged_volume.rolling(48, min_periods=12).median().replace(0, np.nan)
    result["relative_volume"] = lagged_volume / volume_median
    volume_sum = lagged_volume.rolling(24, min_periods=12).sum().replace(0, np.nan)
    result["volume_concentration"] = lagged_volume.rolling(24, min_periods=12).max() / volume_sum

    if "spread_bps" in frame:
        spread = pd.to_numeric(frame["spread_bps"], errors="coerce").astype(float).shift(1)
        if (spread.dropna() < 0).any() or not np.isfinite(spread.dropna().to_numpy()).all():
            raise ValueError("spread evidence must be finite and nonnegative")
        spread_baseline = spread.rolling(48, min_periods=12).median().replace(0, np.nan)
        result["relative_spread"] = spread / spread_baseline
    else:
        result["relative_spread"] = 0.0

    if {"bid_depth_notional", "ask_depth_notional"} <= set(frame.columns):
        bid = pd.to_numeric(frame["bid_depth_notional"], errors="coerce").astype(float).shift(1)
        ask = pd.to_numeric(frame["ask_depth_notional"], errors="coerce").astype(float).shift(1)
        if (pd.concat((bid, ask)).dropna() < 0).any() or not np.isfinite(
            pd.concat((bid, ask)).dropna().to_numpy()
        ).all():
            raise ValueError("depth evidence must be finite and nonnegative")
        result["depth_imbalance"] = (bid - ask) / (bid + ask).replace(0, np.nan)
    else:
        result["depth_imbalance"] = 0.0

    if "market_breadth" in frame:
        breadth = pd.to_numeric(frame["market_breadth"], errors="coerce").astype(float).shift(1)
        if not np.isfinite(breadth.dropna().to_numpy()).all() or not breadth.dropna().between(-1, 1).all():
            raise ValueError("market breadth must be finite and in [-1, 1]")
        result["market_breadth"] = breadth
    else:
        result["market_breadth"] = 0.0

    result["gap_return"] = (numeric["open"] / numeric["close"].shift(1) - 1.0).shift(1)
    intervals = frame["open_timestamp"].diff()
    positive_intervals = intervals[intervals > pd.Timedelta(0)]
    expected_interval = positive_intervals.mode().iloc[0] if not positive_intervals.empty else None
    if expected_interval is None:
        result["continuity"] = np.nan
    else:
        result["continuity"] = intervals.eq(expected_interval).astype(float).shift(1)

    result["available_at"] = frame["available_at"]
    result["source_row"] = np.arange(len(frame), dtype=int)
    result.attrs["causal_lag_bars"] = 1
    return result


def _training_boundary(frame: pd.DataFrame) -> datetime | None:
    if "available_at" not in frame or frame.empty:
        return None
    value = pd.Timestamp(frame["available_at"].max()).to_pydatetime()
    if value.tzinfo is not UTC:
        raise ValueError("training boundary must be explicit UTC")
    return value


def _fallback_fit(features: pd.DataFrame, valid_rows: int, reason: str) -> RegimeFit:
    boundary = _training_boundary(features)
    payload = {
        "status": "parent_fallback",
        "reason": reason,
        "features": REGIME_FEATURE_COLUMNS,
        "training_rows": valid_rows,
        "training_through": boundary,
        "prior": {key.value: value for key, value in _PRIOR.items()},
    }
    return RegimeFit(
        status="parent_fallback",
        feature_names=REGIME_FEATURE_COLUMNS,
        classes=tuple(MarketRegime),
        training_rows=valid_rows,
        training_through=boundary,
        thresholds=MappingProxyType({"reason": reason}),
        scaler_mean=(),
        scaler_scale=(),
        coefficients=(),
        intercepts=(),
        model_hash=canonical_hash(payload),
    )


def fit_regime_model(features: pd.DataFrame, *, minimum_train: int = 80) -> RegimeFit:
    """Fit a fixed-taxonomy regularized model using the supplied chronological prefix only."""

    if minimum_train < 40:
        raise ValueError("minimum_train must be at least 40")
    missing = sorted(set(REGIME_FEATURE_COLUMNS) - set(features.columns))
    if missing:
        raise ValueError(f"features missing regime columns: {', '.join(missing)}")
    matrix = features.loc[:, REGIME_FEATURE_COLUMNS].apply(pd.to_numeric, errors="coerce")
    finite = np.isfinite(matrix.to_numpy(dtype=float)).all(axis=1)
    valid = matrix.loc[finite]
    if len(valid) < minimum_train:
        return _fallback_fit(features.loc[finite], len(valid), "insufficient_training_rows")

    thresholds = {
        "stressed_volatility_percentile": float(valid["volatility_percentile"].quantile(0.85)),
        "stressed_relative_spread": float(valid["relative_spread"].quantile(0.90)),
        "trend_strength": float(valid["trend_strength"].quantile(0.55)),
        "directional_consistency": float(valid["directional_consistency"].quantile(0.55)),
        "elevated_volatility": float(valid["realized_volatility_short"].quantile(0.60)),
    }
    stressed = (
        (valid["volatility_percentile"] >= thresholds["stressed_volatility_percentile"])
        | (
            (valid["relative_spread"] > 0)
            & (valid["relative_spread"] >= thresholds["stressed_relative_spread"])
        )
        | (valid["continuity"] < 0.5)
    )
    trending = (
        (valid["trend_strength"] >= thresholds["trend_strength"])
        & (valid["directional_consistency"] >= thresholds["directional_consistency"])
    )
    elevated = valid["realized_volatility_short"] >= thresholds["elevated_volatility"]
    labels = np.full(len(valid), MarketRegime.RANGE_LIQUID.value, dtype=object)
    labels[trending.to_numpy() & ~elevated.to_numpy()] = MarketRegime.TREND_NORMAL.value
    labels[trending.to_numpy() & elevated.to_numpy()] = MarketRegime.TREND_ELEVATED_VOLATILITY.value
    labels[stressed.to_numpy()] = MarketRegime.STRESSED_OR_ILLIQUID.value
    if len(set(labels)) < 3:
        return _fallback_fit(features.loc[finite], len(valid), "insufficient_regime_classes")

    scaler = StandardScaler()
    scaled = scaler.fit_transform(valid.to_numpy(dtype=float))
    classifier = LogisticRegression(
        C=0.25,
        class_weight="balanced",
        random_state=0,
        max_iter=2_000,
        solver="lbfgs",
    )
    classifier.fit(scaled, labels)
    boundary = _training_boundary(features.loc[finite])
    scaler_mean = tuple(float(value) for value in scaler.mean_)
    scaler_scale = tuple(float(value) for value in scaler.scale_)
    coefficients = tuple(tuple(float(value) for value in row) for row in classifier.coef_)
    intercepts = tuple(float(value) for value in classifier.intercept_)
    payload = {
        "status": "fitted",
        "features": REGIME_FEATURE_COLUMNS,
        "classes": tuple(str(value) for value in classifier.classes_),
        "training_rows": len(valid),
        "training_through": boundary,
        "thresholds": thresholds,
        "scaler_mean": scaler_mean,
        "scaler_scale": scaler_scale,
        "coefficients": coefficients,
        "intercepts": intercepts,
    }
    return RegimeFit(
        status="fitted",
        feature_names=REGIME_FEATURE_COLUMNS,
        classes=tuple(MarketRegime),
        training_rows=len(valid),
        training_through=boundary,
        thresholds=MappingProxyType(thresholds),
        scaler_mean=scaler_mean,
        scaler_scale=scaler_scale,
        coefficients=coefficients,
        intercepts=intercepts,
        model_hash=canonical_hash(payload),
        scaler=scaler,
        classifier=classifier,
    )


def predict_regime_posteriors(fit: RegimeFit, features: pd.DataFrame) -> RegimePosteriorFrame:
    """Return normalized soft probabilities, conservatively falling back for unavailable rows."""

    missing = sorted(set(fit.feature_names) - set(features.columns))
    if missing:
        raise ValueError(f"features missing fitted regime columns: {', '.join(missing)}")
    regimes = tuple(MarketRegime)
    prior = np.array([_PRIOR[regime] for regime in regimes], dtype=float)
    probabilities = np.tile(prior, (len(features), 1))
    matrix = features.loc[:, fit.feature_names].apply(pd.to_numeric, errors="coerce")
    valid = np.isfinite(matrix.to_numpy(dtype=float)).all(axis=1)
    if fit.status == "fitted":
        if fit.scaler is None or fit.classifier is None:
            raise ValueError("fitted regime evidence is missing its authenticated estimator")
        if valid.any():
            predicted = fit.classifier.predict_proba(
                fit.scaler.transform(matrix.loc[valid].to_numpy(dtype=float))
            )
            aligned = np.zeros((len(predicted), len(regimes)), dtype=float)
            class_to_column = {regime.value: index for index, regime in enumerate(regimes)}
            for source_column, label in enumerate(fit.classifier.classes_):
                aligned[:, class_to_column[str(label)]] = predicted[:, source_column]
            aligned = 0.95 * aligned + 0.05 * prior
            aligned /= aligned.sum(axis=1, keepdims=True)
            probabilities[valid] = aligned
    if not np.isfinite(probabilities).all() or (probabilities < 0).any():
        raise ValueError("regime posterior contains invalid probability mass")
    probabilities /= probabilities.sum(axis=1, keepdims=True)
    probabilities.setflags(write=False)

    evidence_through = _training_boundary(features.loc[valid]) if valid.any() else None
    posterior_hash = canonical_hash(
        {
            "model_hash": fit.model_hash,
            "index": tuple(str(value) for value in features.index),
            "probabilities": tuple(tuple(float(value) for value in row) for row in probabilities),
            "evidence_through": evidence_through,
        }
    )
    return RegimePosteriorFrame(
        probabilities=probabilities,
        regimes=regimes,
        index=tuple(features.index),
        status=fit.status,
        model_hash=fit.model_hash,
        evidence_through=evidence_through,
        posterior_hash=posterior_hash,
    )


__all__ = [
    "REGIME_FEATURE_COLUMNS",
    "RegimeFit",
    "RegimePosteriorFrame",
    "causal_regime_features",
    "fit_regime_model",
    "predict_regime_posteriors",
]
