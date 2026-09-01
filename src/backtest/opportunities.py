"""Causal, research-only scoring of discrete intraday trading opportunities."""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd

from src.backtest.robustness import lower_mean_confidence_bound, run_block_bootstrap
from src.models.trade_outcomes import BarrierPolicy, TradeOutcome


@dataclass(frozen=True, slots=True)
class OpportunityAuditResult:
    outcomes: pd.DataFrame
    diagnostics: dict[str, int]


_OUTCOME_COLUMNS = [
    *TradeOutcome.model_fields,
    "strategy_id",
    "family",
    "signal_strength",
    "signal_reason",
    "round_trip_cost_bps",
]


def _utc_series(frame: pd.DataFrame, name: str) -> pd.Series:
    for value in frame[name].dropna():
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
            raise ValueError(f"{name} must contain explicit UTC timestamps")
    return pd.to_datetime(frame[name], utc=True)


def _validated_bars(frame: pd.DataFrame, policy: BarrierPolicy) -> pd.DataFrame:
    required = {
        "open_timestamp",
        "close_timestamp",
        "available_at",
        "finalized",
        "open",
        "high",
        "low",
        "close",
    }
    missing = required - set(frame)
    if missing:
        raise ValueError(f"opportunity bars are missing fields: {sorted(missing)}")
    result = frame.copy()
    for name in ("open_timestamp", "close_timestamp", "available_at"):
        result[name] = _utc_series(result, name)
    result = result.sort_values("open_timestamp", kind="stable").reset_index(drop=True)
    if result["open_timestamp"].duplicated().any():
        raise ValueError("opportunity bars must have unique open timestamps")
    if not (result["open_timestamp"] < result["close_timestamp"]).all():
        raise ValueError("opportunity bar close must follow open")
    if not (result["available_at"] >= result["close_timestamp"]).all():
        raise ValueError("opportunity bars cannot be available before close")
    if not result["finalized"].map(lambda value: isinstance(value, (bool, np.bool_)) and bool(value)).all():
        raise ValueError("opportunity bars must all be explicitly finalized")
    for name in ("open", "high", "low", "close"):
        result[name] = pd.to_numeric(result[name], errors="raise")
        if not result[name].map(math.isfinite).all() or not (result[name] > 0).all():
            raise ValueError("opportunity prices must be positive and finite")
    if not (
        (result["high"] >= result[["open", "close"]].max(axis=1))
        & (result["low"] <= result[["open", "close"]].min(axis=1))
        & (result["high"] >= result["low"])
    ).all():
        raise ValueError("opportunity bars contain impossible OHLC values")
    risk = result[policy.risk_column] if policy.risk_column in result else result["high"] - result["low"]
    result["_risk"] = pd.to_numeric(risk, errors="coerce")
    return result


def _validated_signals(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"decision_timestamp", "data_through", "signal", "strength"}
    missing = required - set(frame)
    if missing:
        raise ValueError(f"opportunity signals are missing fields: {sorted(missing)}")
    result = frame.copy()
    for name in ("decision_timestamp", "data_through"):
        result[name] = _utc_series(result, name)
    if (result["decision_timestamp"] < result["data_through"]).any():
        raise ValueError("opportunity decision is noncausal")
    if result["decision_timestamp"].duplicated().any():
        raise ValueError("opportunity decisions must be unique")
    result["signal"] = pd.to_numeric(result["signal"], errors="raise").astype(int)
    if not result["signal"].isin((-1, 0, 1)).all():
        raise ValueError("opportunity signal must be -1, 0, or 1")
    result["strength"] = pd.to_numeric(result["strength"], errors="raise")
    if not result["strength"].map(math.isfinite).all() or not result["strength"].between(0, 1).all():
        raise ValueError("opportunity strength must be finite and in [0, 1]")
    if "reason" not in result:
        result["reason"] = "configured strategy condition"
    else:
        result["reason"] = result["reason"].astype(str)
    return result.sort_values("decision_timestamp", kind="stable").reset_index(drop=True)


def _candidate_outcome(
    bars: pd.DataFrame,
    decision_index: int,
    signal: pd.Series,
    policy: BarrierPolicy,
) -> tuple[TradeOutcome | None, str | None]:
    decision = bars.iloc[decision_index]
    if pd.Timestamp(signal["decision_timestamp"]) < pd.Timestamp(decision["available_at"]):
        raise ValueError("opportunity decision predates finalized-bar availability")
    future = bars.iloc[decision_index + 1 : decision_index + 1 + policy.maximum_bars]
    if future.empty:
        return None, "right_censored"
    if pd.Timestamp(signal["decision_timestamp"]) > pd.Timestamp(future.iloc[0]["open_timestamp"]):
        return None, "late_decision"

    expected_open = pd.Timestamp(decision["close_timestamp"])
    contiguous: list[pd.Series] = []
    gap_found = False
    for _, row in future.iterrows():
        if pd.Timestamp(row["open_timestamp"]) != expected_open:
            gap_found = True
            break
        contiguous.append(row)
        expected_open = pd.Timestamp(row["close_timestamp"])
    if not contiguous:
        return None, "gap_blocked"

    risk = float(decision["_risk"])
    if not math.isfinite(risk) or risk <= 0:
        return None, "invalid_risk"
    direction = "long" if int(signal["signal"]) > 0 else "short"
    entry = float(contiguous[0]["open"])
    if direction == "long":
        stop, target = entry - policy.stop_r * risk, entry + policy.target_r * risk
    else:
        stop, target = entry + policy.stop_r * risk, entry - policy.target_r * risk
    if stop <= 0 or target <= 0:
        return None, "invalid_risk"

    maximum_favourable = 0.0
    maximum_adverse = 0.0
    exit_price = float(contiguous[-1]["close"])
    exit_reason = "expired"
    exit_row = contiguous[-1]
    bars_held = len(contiguous)
    barrier_touched = False
    for ordinal, row in enumerate(contiguous, start=1):
        opening, high, low = (float(row[name]) for name in ("open", "high", "low"))
        if direction == "long":
            maximum_favourable = max(maximum_favourable, (high - entry) / risk)
            maximum_adverse = max(maximum_adverse, (entry - low) / risk)
            stop_touched, target_touched = low <= stop, high >= target
            adverse_exit = min(stop, opening) if opening <= stop else stop
        else:
            maximum_favourable = max(maximum_favourable, (entry - low) / risk)
            maximum_adverse = max(maximum_adverse, (high - entry) / risk)
            stop_touched, target_touched = high >= stop, low <= target
            adverse_exit = max(stop, opening) if opening >= stop else stop
        if stop_touched and target_touched:
            exit_price, exit_reason = adverse_exit, "ambiguous_stop_first"
        elif stop_touched:
            exit_price, exit_reason = adverse_exit, "stop"
        elif ordinal == policy.maximum_bars:
            exit_price, exit_reason = float(row["close"]), "expired"
        elif target_touched:
            exit_price, exit_reason = target, "target"
        else:
            continue
        exit_row, bars_held, barrier_touched = row, ordinal, True
        break

    if not barrier_touched and len(contiguous) < policy.maximum_bars:
        return None, "gap_truncated" if gap_found else "right_censored"
    sign = 1.0 if direction == "long" else -1.0
    gross_return = sign * (exit_price / entry - 1.0)
    net_return = gross_return - policy.round_trip_cost_bps / 10_000
    return (
        TradeOutcome(
            provider=str(decision.get("provider", "unknown")),
            feed=str(decision.get("feed", "unknown")),
            symbol=str(decision.get("symbol", "unknown")).upper(),
            direction=direction,
            decision_timestamp=pd.Timestamp(signal["decision_timestamp"]).to_pydatetime(),
            entry_timestamp=pd.Timestamp(contiguous[0]["open_timestamp"]).to_pydatetime(),
            exit_timestamp=pd.Timestamp(exit_row["close_timestamp"]).to_pydatetime(),
            outcome_available_at=pd.Timestamp(exit_row["available_at"]).to_pydatetime(),
            entry_price=entry,
            stop_price=stop,
            target_price=target,
            exit_price=exit_price,
            risk_distance=risk,
            target_before_stop=exit_reason == "target",
            exit_reason=exit_reason,
            gross_return=gross_return,
            net_return=net_return,
            maximum_favourable_excursion_r=maximum_favourable,
            maximum_adverse_excursion_r=maximum_adverse,
            bars_held=bars_held,
        ),
        None,
    )


def audit_strategy_opportunities(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    policy: BarrierPolicy,
    *,
    strategy_id: str,
    family: str,
    minimum_strength: float = 0.0,
) -> OpportunityAuditResult:
    """Score non-overlapping signals without allowing gaps, repainting, or tail censoring."""

    if not strategy_id.strip() or not family.strip():
        raise ValueError("strategy identity must not be empty")
    if not math.isfinite(minimum_strength) or not 0 <= minimum_strength <= 1:
        raise ValueError("minimum strength must be in [0, 1]")
    ordered_bars = _validated_bars(bars, policy)
    ordered_signals = _validated_signals(signals)
    close_to_index = {timestamp: index for index, timestamp in enumerate(ordered_bars["close_timestamp"])}
    diagnostics: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    busy_until: pd.Timestamp | None = None
    for _, signal in ordered_signals.iterrows():
        if int(signal["signal"]) == 0 or float(signal["strength"]) < minimum_strength:
            continue
        diagnostics["signals_considered"] += 1
        data_through = pd.Timestamp(signal["data_through"])
        if data_through not in close_to_index:
            raise ValueError("opportunity signal has no matching finalized bar")
        if busy_until is not None and pd.Timestamp(signal["decision_timestamp"]) < busy_until:
            diagnostics["overlap_blocked"] += 1
            continue
        outcome, rejection = _candidate_outcome(ordered_bars, close_to_index[data_through], signal, policy)
        if outcome is None:
            diagnostics[rejection or "unscorable"] += 1
            continue
        payload = outcome.model_dump()
        payload.update(
            {
                "strategy_id": strategy_id.strip(),
                "family": family.strip(),
                "signal_strength": float(signal["strength"]),
                "signal_reason": str(signal["reason"]),
                "round_trip_cost_bps": float(policy.round_trip_cost_bps),
            }
        )
        rows.append(payload)
        busy_until = pd.Timestamp(outcome.exit_timestamp)
    diagnostics["scored_opportunities"] = len(rows)
    for name in (
        "signals_considered",
        "overlap_blocked",
        "gap_blocked",
        "gap_truncated",
        "right_censored",
        "late_decision",
        "invalid_risk",
    ):
        diagnostics[name] += 0
    return OpportunityAuditResult(pd.DataFrame(rows, columns=_OUTCOME_COLUMNS), dict(sorted(diagnostics.items())))


def _finite_or_none(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def summarize_opportunities(
    outcomes: pd.DataFrame,
    *,
    bootstrap_samples: int = 2_000,
    seed: int = 42,
) -> dict[str, Any]:
    """Return bounded retrospective diagnostics that can never authorize live use."""

    if bootstrap_samples < 1:
        raise ValueError("bootstrap samples must be positive")
    if outcomes.empty:
        return {
            "evidence_tier": "retrospective_archive_only",
            "eligible_for_live_promotion": False,
            "opportunities": 0,
            "note": "No scorable non-overlapping opportunities were identified.",
        }
    required = {"net_return", "gross_return", "target_before_stop", "exit_reason", "direction"}
    missing = required - set(outcomes)
    if missing:
        raise ValueError(f"opportunity outcomes are missing fields: {sorted(missing)}")
    net = pd.to_numeric(outcomes["net_return"], errors="coerce").to_numpy(dtype=float)
    gross = pd.to_numeric(outcomes["gross_return"], errors="coerce").to_numpy(dtype=float)
    if np.any(~np.isfinite(net)) or np.any(~np.isfinite(gross)) or np.any(net <= -1):
        raise ValueError("opportunity returns must be finite and greater than -100%")
    wealth = np.cumprod(1 + net)
    peaks = np.maximum.accumulate(np.concatenate(([1.0], wealth)))
    equity = np.concatenate(([1.0], wealth))
    maximum_drawdown = float(np.min(equity / peaks - 1))
    winners = net[net > 0]
    losers = net[net < 0]
    bootstrap = run_block_bootstrap(net, samples=bootstrap_samples, seed=seed) if len(net) >= 2 else None
    lower = lower_mean_confidence_bound(net) if len(net) >= 2 else None
    return {
        "evidence_tier": "retrospective_archive_only",
        "eligible_for_live_promotion": False,
        "opportunities": int(len(net)),
        "long_opportunities": int((outcomes["direction"] == "long").sum()),
        "short_hypotheses": int((outcomes["direction"] == "short").sum()),
        "targets": int((outcomes["exit_reason"] == "target").sum()),
        "stops": int(outcomes["exit_reason"].isin(("stop", "ambiguous_stop_first")).sum()),
        "expired": int((outcomes["exit_reason"] == "expired").sum()),
        "target_before_stop_rate": float(pd.to_numeric(outcomes["target_before_stop"]).mean()),
        "net_win_rate": float(np.mean(net > 0)),
        "mean_gross_return": float(gross.mean()),
        "mean_net_return": float(net.mean()),
        "median_net_return": float(np.median(net)),
        "lower_mean_net_return": _finite_or_none(lower) if lower is not None else None,
        "cumulative_net_return": float(wealth[-1] - 1),
        "maximum_drawdown": maximum_drawdown,
        "profit_factor": _finite_or_none(float(winners.sum() / abs(losers.sum()))) if len(losers) else None,
        "bootstrap_ci_low": bootstrap.ci_low if bootstrap is not None else None,
        "bootstrap_ci_high": bootstrap.ci_high if bootstrap is not None else None,
        "bootstrap_probability_positive": bootstrap.probability_positive if bootstrap is not None else None,
        "note": (
            "Retrospective archive evidence can reject a strategy but cannot qualify alerts or establish "
            "future profitability. Short hypotheses also require a separately validated shortable product."
        ),
    }


__all__ = ["OpportunityAuditResult", "audit_strategy_opportunities", "summarize_opportunities"]
