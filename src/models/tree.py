from __future__ import annotations

from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from src.models.base import ModelSpec


def build_tree_pipeline(spec: ModelSpec, numeric_columns: list[str], seed: int) -> Pipeline:
    preprocess = ColumnTransformer(
        [
            ("numeric", SimpleImputer(strategy="median"), numeric_columns),
            (
                "categorical",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
                    ]
                ),
                ["company_id"],
            ),
        ]
    )
    model = HistGradientBoostingRegressor(
        max_iter=int(spec.parameters.get("max_iter", 100)),
        learning_rate=float(spec.parameters.get("learning_rate", 0.05)),
        max_leaf_nodes=int(spec.parameters.get("max_leaf_nodes", 15)),
        l2_regularization=float(spec.parameters.get("l2_regularization", 1.0)),
        random_state=seed,
    )
    return Pipeline([("preprocess", preprocess), ("model", model)])
