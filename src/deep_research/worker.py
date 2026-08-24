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
    folds = tuple(_fold_metric(tuple(values)) for values in work.fold_returns)
    sharpes = np.asarray([fold.net_sharpe for fold in folds], dtype=float)
    drawdowns = np.asarray([fold.maximum_drawdown for fold in folds], dtype=float)
    fitness = float(np.median(sharpes) - np.median(drawdowns))
    return WorkerResult(
        ordinal=work.ordinal,
        folds=folds,
        fitness=fitness,
        thread_limit=os.environ.get("OMP_NUM_THREADS", ""),
    )


__all__ = [
    "NUMERIC_THREAD_ENVIRONMENT",
    "WorkerFoldMetric",
    "WorkerResult",
    "configure_worker_environment",
    "evaluate_candidate_work",
]
