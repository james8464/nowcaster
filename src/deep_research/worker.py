from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass

import numpy as np

NUMERIC_THREAD_ENVIRONMENT = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
)


def configure_worker_environment() -> None:
    for name in NUMERIC_THREAD_ENVIRONMENT:
        os.environ[name] = "1"


@dataclass(frozen=True, slots=True)
class WorkerFoldMetric:
    net_return: float
    net_sharpe: float
    maximum_drawdown: float
    observations: int


@dataclass(frozen=True, slots=True)
class WorkerResult:
    ordinal: int
    folds: tuple[WorkerFoldMetric, ...]
    fitness: float
    thread_limit: str
    gross_returns: tuple[float, ...]
    costs: tuple[float, ...]
    trade_count: int


def _fold_metric(values: tuple[float, ...]) -> WorkerFoldMetric:
    returns = np.asarray(values, dtype=float)
    if not len(returns) or np.any(~np.isfinite(returns)):
        raise ValueError("worker folds require finite returns")
    equity = np.cumprod(1 + np.clip(returns, -0.999999, None))
    peaks = np.maximum.accumulate(equity)
    drawdown = float(np.max((peaks - equity) / peaks))
    deviation = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    sharpe = float(np.mean(returns) / deviation * math.sqrt(len(returns))) if deviation > 0 else 0.0
    return WorkerFoldMetric(float(equity[-1] - 1), sharpe, drawdown, len(returns))


def evaluate_candidate_work(work, attempt_number: int) -> WorkerResult:
    configure_worker_environment()
    if work.delay_seconds:
        time.sleep(work.delay_seconds)
    if attempt_number <= work.failures_before_success:
        raise RuntimeError(f"injected worker failure {attempt_number}")
    if work.evaluation_payload is not None:
        from src.deep_research.evaluation import evaluate_candidate_payload

        evaluated = evaluate_candidate_payload(work.evaluation_payload)
        fold_returns = evaluated.fold_returns
        gross_returns = evaluated.gross_returns
        costs = evaluated.costs
        trade_count = evaluated.trade_count
    else:
        fold_returns = work.fold_returns
        gross_returns = work.gross_returns
        costs = work.costs
        trade_count = work.trade_count if work.trade_count is not None else len(gross_returns)
    folds = tuple(_fold_metric(tuple(values)) for values in fold_returns)
    sharpes = np.asarray([fold.net_sharpe for fold in folds], dtype=float)
    drawdowns = np.asarray([fold.maximum_drawdown for fold in folds], dtype=float)
    fitness = float(np.median(sharpes) - np.median(drawdowns))
    return WorkerResult(
        ordinal=work.ordinal,
        folds=folds,
        fitness=fitness,
        thread_limit=os.environ.get("OMP_NUM_THREADS", ""),
        gross_returns=tuple(gross_returns),
        costs=tuple(costs),
        trade_count=trade_count,
    )


__all__ = [
    "NUMERIC_THREAD_ENVIRONMENT",
    "WorkerFoldMetric",
    "WorkerResult",
    "configure_worker_environment",
    "evaluate_candidate_work",
]
