from __future__ import annotations

import pandas as pd

from src.backtest.protocol import WalkForwardProtocol


def test_protocol_reserves_final_test_and_embargoes_overlap() -> None:
    rows = pd.DataFrame(
        {
            "decision_date": pd.date_range("2022-01-01", periods=300, freq="D"),
            "label_end": pd.date_range("2022-01-06", periods=300, freq="D"),
        }
    )
    protocol = WalkForwardProtocol(final_test_fraction=0.2, minimum_train=100, validation_size=30, embargo=5)
    folds = protocol.split(rows, decision_column="decision_date", label_end_column="label_end")
    assert folds
    assert all(fold.train_label_end < fold.validation_start for fold in folds)
    assert min(protocol.final_test_indices) > max(protocol.development_indices)
    assert not set(protocol.final_test_indices).intersection(protocol.development_indices)


def test_protocol_rejects_invalid_dates() -> None:
    rows = pd.DataFrame({"decision_date": ["bad"], "label_end": ["bad"]})
    protocol = WalkForwardProtocol(minimum_train=10)
    try:
        protocol.split(rows, decision_column="decision_date", label_end_column="label_end")
    except ValueError as error:
        assert "date" in str(error).lower()
    else:
        raise AssertionError("invalid dates should fail")
