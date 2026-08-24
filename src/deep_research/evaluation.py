from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from src.backtest.execution import ExecutionAssumptions
from src.backtest.intraday import RiskLimits, run_intraday_backtest
from src.deep_research.candidates import CandidateDefinition
from src.strategies.indicators import rolling_zscore, rsi
from src.strategies.library import StrategyContext, generate_signals
from src.strategies.types import StrategySpec


@dataclass(frozen=True, slots=True)
class CandidateEvaluationPayload:
    candidate: CandidateDefinition
    base_spec_payload: dict[str, object]
    signal_bars: pd.DataFrame
    causal_bars: pd.DataFrame
    provider: str
    feed: str
    symbol: str
    evaluation_start: datetime
    evaluation_end: datetime
    fold_ranges: tuple[tuple[datetime, datetime], ...]
    execution_assumptions: ExecutionAssumptions
    risk_limits: RiskLimits

    @property
    def base_spec(self) -> StrategySpec:
        return StrategySpec.model_validate(self.base_spec_payload)


@dataclass(frozen=True, slots=True)
class CandidatePathEvidence:
    fold_returns: tuple[tuple[float, ...], ...]
    gross_returns: tuple[float, ...]
    costs: tuple[float, ...]
    trade_count: int


def _rule_signals(payload: CandidateEvaluationPayload) -> pd.DataFrame:
    rule = payload.candidate.rule
    if rule is None:
        raise ValueError("rule evaluation requires a typed rule")
    bars = payload.causal_bars.sort_values("open_timestamp", kind="stable").reset_index(drop=True).copy()
    bars["rsi"] = rsi(bars["close"], min(14, max(2, payload.base_spec.warmup_bars)))
    bars["volume_zscore"] = rolling_zscore(bars["volume"], min(20, max(3, payload.base_spec.warmup_bars)))
    active = rule.evaluate(bars).fillna(False).astype(bool)
    return pd.DataFrame(
        {
            "decision_timestamp": pd.to_datetime(bars["close_timestamp"], utc=True),
            "data_through": pd.to_datetime(bars["close_timestamp"], utc=True),
            "signal": np.where(active, 1, -1),
            "strength": 1.0,
        }
    )


def _candidate_signals(payload: CandidateEvaluationPayload) -> pd.DataFrame:
    if payload.candidate.rule is not None:
        return _rule_signals(payload)
    parameters = dict(payload.candidate.parameters) or dict(payload.base_spec.parameters)
    spec = payload.base_spec.model_copy(update={"parameters": parameters})
    return generate_signals(
        spec,
        payload.signal_bars,
        StrategyContext.for_market(payload.provider, payload.feed),
    )


def evaluate_candidate_payload(payload: CandidateEvaluationPayload) -> CandidatePathEvidence:
    start = pd.Timestamp(payload.evaluation_start)
    end = pd.Timestamp(payload.evaluation_end)
    if start.tzinfo is None or end.tzinfo is None or start >= end:
        raise ValueError("candidate evaluation requires an ordered timezone-aware interval")
    bars = payload.causal_bars.copy(deep=True)
    bars["close_timestamp"] = pd.to_datetime(bars["close_timestamp"], utc=True)
    bars = bars.loc[bars["close_timestamp"] < end].copy()
    signals = _candidate_signals(payload)
    signals["decision_timestamp"] = pd.to_datetime(signals["decision_timestamp"], utc=True)
    signals = signals.loc[signals["decision_timestamp"] < end].copy()
    result = run_intraday_backtest(
        bars,
        signals,
        payload.execution_assumptions,
        payload.risk_limits,
        strategy_id=payload.base_spec.strategy_id,
        symbol=payload.symbol,
    )
    curve = result.equity_curve.copy()
    curve["timestamp"] = pd.to_datetime(curve["timestamp"], utc=True)
    curve = curve.loc[(curve["timestamp"] >= start) & (curve["timestamp"] < end)].copy()
    if curve.empty:
        raise ValueError("candidate produced no executable observations in the evaluation interval")
    net = pd.to_numeric(curve["net_return"], errors="coerce")
    gross = pd.to_numeric(curve["gross_return"], errors="coerce")
    costs = pd.to_numeric(curve["cost_return"], errors="coerce")
    if pd.concat([net, gross, costs], axis=1).isna().any().any():
        raise ValueError("candidate execution produced non-finite returns")
    fold_returns: list[tuple[float, ...]] = []
    for fold_start, fold_end in payload.fold_ranges:
        selected = curve.loc[
            (curve["timestamp"] >= pd.Timestamp(fold_start)) & (curve["timestamp"] < pd.Timestamp(fold_end)),
            "net_return",
        ]
        if selected.empty:
            raise ValueError("candidate walk-forward fold has no execution observations")
        fold_returns.append(tuple(float(value) for value in selected))
    return CandidatePathEvidence(
        fold_returns=tuple(fold_returns),
        gross_returns=tuple(float(value) for value in gross),
        costs=tuple(float(value) for value in costs),
        trade_count=len(result.trade_ledger) // 2,
    )


__all__ = ["CandidateEvaluationPayload", "CandidatePathEvidence", "evaluate_candidate_payload"]
