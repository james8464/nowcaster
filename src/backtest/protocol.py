from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ProtocolFold:
    train_indices: np.ndarray
    validation_indices: np.ndarray
    train_label_end: object
    validation_start: object
    validation_end: object


class WalkForwardProtocol:
    """Purged expanding-window protocol with a never-touched final test slice."""

    def __init__(
        self,
        *,
        final_test_fraction: float = 0.2,
        minimum_train: int = 100,
        validation_size: int = 30,
        embargo: int = 5,
    ):
        if not 0 < final_test_fraction < 0.5:
            raise ValueError("final_test_fraction must be in (0, 0.5)")
        if minimum_train <= 0 or validation_size <= 0 or embargo < 0:
            raise ValueError("training and validation sizes must be positive; embargo cannot be negative")
        self.final_test_fraction = final_test_fraction
        self.minimum_train = minimum_train
        self.validation_size = validation_size
        self.embargo = embargo
        self.development_indices = np.array([], dtype=int)
        self.final_test_indices = np.array([], dtype=int)

    def split(
        self,
        rows: pd.DataFrame,
        *,
        decision_column: str,
        label_end_column: str,
    ) -> list[ProtocolFold]:
        if decision_column not in rows or label_end_column not in rows:
            raise ValueError("decision and label-end columns are required")
        decisions = pd.to_datetime(rows[decision_column], errors="coerce")
        label_ends = pd.to_datetime(rows[label_end_column], errors="coerce")
        if decisions.isna().any() or label_ends.isna().any():
            raise ValueError("decision and label-end dates must be valid")
        order = np.argsort(decisions.to_numpy(), kind="stable")
        ordered_decisions = decisions.iloc[order].reset_index(drop=True)
        ordered_labels = label_ends.iloc[order].reset_index(drop=True)
        final_size = max(1, int(np.ceil(len(rows) * self.final_test_fraction)))
        development_size = len(rows) - final_size
        self.development_indices = order[:development_size]
        self.final_test_indices = order[development_size:]

        folds: list[ProtocolFold] = []
        start = self.minimum_train
        while start < development_size:
            end = min(start + self.validation_size, development_size)
            validation_start = ordered_decisions.iloc[start]
            purge_before = validation_start - pd.Timedelta(days=self.embargo)
            train_ordered = np.flatnonzero(
                (np.arange(len(rows)) < start) & (ordered_labels < purge_before).to_numpy()
            )
            if len(train_ordered) >= self.minimum_train:
                validation_ordered = np.arange(start, end)
                folds.append(
                    ProtocolFold(
                        train_indices=order[train_ordered],
                        validation_indices=order[validation_ordered],
                        train_label_end=ordered_labels.iloc[train_ordered].max().date(),
                        validation_start=validation_start.date(),
                        validation_end=ordered_decisions.iloc[end - 1].date(),
                    )
                )
            start = end
        return folds

