from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from src.crypto.features import CRYPTO_FEATURE_COLUMNS
from src.utils.provenance import canonical_hash


def crypto_feature_rows(frame: pd.DataFrame, *, source_version: str) -> list[dict[str, object]]:
    created_at = datetime.now(UTC)
    rows: list[dict[str, object]] = []
    for row in frame.to_dict(orient="records"):
        values = {key: row[key] for key in frame.columns}
        values.update(
            {
                "feature_id": canonical_hash([row["symbol"], row["decision_date"], row["horizon_days"]])[:24],
                "source": "point_in_time_crypto_features",
                "source_version": source_version,
                "created_at": created_at,
            }
        )
        rows.append(values)
    return rows


def crypto_signal_rows(frame: pd.DataFrame, *, source_version: str) -> list[dict[str, object]]:
    created_at = datetime.now(UTC)
    rows: list[dict[str, object]] = []
    for row in frame.to_dict(orient="records"):
        rows.append(
            {
                "signal_id": canonical_hash(
                    [row["symbol"], row["decision_date"], row["horizon_days"], row["model_name"]]
                )[:24],
                "instrument_id": row["symbol"],
                "symbol": row["symbol"],
                "asset_class": "crypto",
                "decision_date": row["decision_date"],
                "data_through_date": row["data_through_date"],
                "execution_date": row["execution_date"],
                "label_end_date": row["label_end"],
                "horizon_days": int(row["horizon_days"]),
                "model_name": row["model_name"],
                "posture": row["posture"],
                "direction_probability": float(row["direction_probability"]),
                "expected_return": float(row["expected_return"]),
                "confidence_score": float(row["confidence_score"]),
                "training_samples": int(row["training_samples"]),
                "calibration_status": row["calibration_status"],
                "status": "out_of_sample_research",
                "explanation": {
                    "model_agreement": float(row["model_agreement"]),
                    "profit_probability": False,
                    "feature_columns": list(CRYPTO_FEATURE_COLUMNS),
                    "execution_lag_bars": 1,
                },
                "source": "purged_walk_forward_crypto_research",
                "source_version": source_version,
                "created_at": created_at,
            }
        )
    return rows
