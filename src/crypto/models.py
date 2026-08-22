from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, mean_absolute_error
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from src.crypto.features import CRYPTO_FEATURE_COLUMNS
from src.models.calibration import RollingProbabilityCalibrator


@dataclass(frozen=True)
class CryptoFold:
    train_indices: np.ndarray
    test_indices: np.ndarray
    training_label_end: object
    test_decision_start: object


@dataclass(frozen=True)
class CryptoModelOutput:
    predictions: pd.DataFrame
    metrics: dict[str, float]


def make_crypto_walk_forward_folds(
    matrix: pd.DataFrame,
    *,
    minimum_train: int = 365,
    test_size: int = 60,
    embargo_days: int = 5,
) -> list[CryptoFold]:
    if minimum_train <= 0 or test_size <= 0 or embargo_days < 0:
        raise ValueError("minimum_train and test_size must be positive; embargo_days cannot be negative")
    ordered = matrix.sort_values("decision_date").reset_index(drop=True)
    decisions = pd.to_datetime(ordered["decision_date"])
    label_ends = pd.to_datetime(ordered["label_end"])
    folds: list[CryptoFold] = []
    start = minimum_train
    while start < len(ordered):
        test_end = min(start + test_size, len(ordered))
        test_start_date = decisions.iloc[start]
        purge_cutoff = test_start_date - pd.Timedelta(days=embargo_days)
        train_indices = np.flatnonzero((decisions < test_start_date) & (label_ends < purge_cutoff))
        test_indices = np.arange(start, test_end)
        if len(train_indices) >= minimum_train and len(test_indices):
            folds.append(
                CryptoFold(
                    train_indices=train_indices,
                    test_indices=test_indices,
                    training_label_end=label_ends.iloc[train_indices].max().date(),
                    test_decision_start=test_start_date.date(),
                )
            )
        start = test_end
    return folds


def _sigmoid(value: np.ndarray) -> np.ndarray:
    return 1 / (1 + np.exp(-np.clip(value, -30, 30)))


def run_crypto_models(
    matrix: pd.DataFrame,
    *,
    minimum_train: int = 365,
    test_size: int = 60,
    seed: int = 42,
) -> CryptoModelOutput:
    """Run deterministic, purged expanding-window crypto models per instrument."""
    outputs: list[pd.DataFrame] = []
    for _symbol, raw in matrix.groupby("symbol", sort=True):
        ordered = raw.sort_values("decision_date").reset_index(drop=True)
        folds = make_crypto_walk_forward_folds(
            ordered,
            minimum_train=minimum_train,
            test_size=test_size,
            embargo_days=int(ordered["horizon_days"].max()),
        )
        past_probabilities: list[float] = []
        past_outcomes: list[int] = []
        for fold_index, fold in enumerate(folds):
            train = ordered.iloc[fold.train_indices]
            test = ordered.iloc[fold.test_indices]
            x_train = train.loc[:, CRYPTO_FEATURE_COLUMNS].to_numpy(float)
            x_test = test.loc[:, CRYPTO_FEATURE_COLUMNS].to_numpy(float)
            y_direction = train["target_direction"].to_numpy(int)
            y_return = train["target_forward_return"].to_numpy(float)

            if len(np.unique(y_direction)) < 2:
                raw_probability = np.full(len(test), float(y_direction.mean()))
            else:
                direction_model = make_pipeline(
                    StandardScaler(),
                    LogisticRegression(C=0.2, max_iter=2_000, random_state=seed),
                )
                direction_model.fit(x_train, y_direction)
                raw_probability = direction_model.predict_proba(x_test)[:, 1]

            return_model = HistGradientBoostingRegressor(
                learning_rate=0.05,
                max_depth=3,
                max_iter=150,
                l2_regularization=1.0,
                random_state=seed,
            )
            return_model.fit(x_train, y_return)
            learned_return = return_model.predict(x_test)
            train_scale = max(float(np.std(y_return, ddof=1)), 1e-6)
            momentum_probability = _sigmoid(test["feature_momentum_20d"].to_numpy(float) / train_scale)
            raw_probability = np.clip(0.75 * raw_probability + 0.25 * momentum_probability, 0, 1)
            calibrator = RollingProbabilityCalibrator(minimum_observations=100)
            calibrator.fit(np.asarray(past_probabilities), np.asarray(past_outcomes))
            probability = calibrator.predict(raw_probability)
            trend_return = test["feature_momentum_20d"].to_numpy(float) * (
                test["horizon_days"].to_numpy(float) / 20
            )
            expected_return = 0.8 * learned_return + 0.2 * trend_return
            confidence = np.clip(np.abs(probability - 0.5) * 2, 0, 1)
            agreement = (
                np.sign(learned_return) == np.sign(test["feature_momentum_20d"].to_numpy(float))
            ).astype(float)
            posture = np.where(
                (probability >= 0.58) & (expected_return > 0) & (agreement > 0),
                "long_research",
                np.where(
                    (probability <= 0.42) & (expected_return < 0) & (agreement > 0),
                    "short_research",
                    "abstain",
                ),
            )
            prediction = test[
                [
                    "symbol",
                    "decision_date",
                    "data_through_date",
                    "execution_date",
                    "label_end",
                    "horizon_days",
                    "target_forward_return",
                    "target_direction",
                ]
            ].copy()
            prediction["model_name"] = "calibrated_logistic_hgb_ensemble"
            prediction["direction_probability"] = probability
            prediction["expected_return"] = expected_return
            prediction["confidence_score"] = confidence * 100
            prediction["posture"] = posture
            prediction["model_agreement"] = agreement
            prediction["training_samples"] = len(train)
            prediction["fold_index"] = fold_index
            prediction["calibration_status"] = calibrator.status
            outputs.append(prediction)
            past_probabilities.extend(raw_probability.tolist())
            past_outcomes.extend(test["target_direction"].astype(int).tolist())

    if not outputs:
        return CryptoModelOutput(pd.DataFrame(), {})
    predictions = pd.concat(outputs, ignore_index=True).sort_values(["decision_date", "symbol"]).reset_index(drop=True)
    metrics = {
        "observations": float(len(predictions)),
        "brier_score": float(brier_score_loss(predictions["target_direction"], predictions["direction_probability"])),
        "return_mae": float(mean_absolute_error(predictions["target_forward_return"], predictions["expected_return"])),
        "directional_accuracy": float(
            ((predictions["direction_probability"] >= 0.5).astype(int) == predictions["target_direction"]).mean()
        ),
    }
    return CryptoModelOutput(predictions, metrics)
