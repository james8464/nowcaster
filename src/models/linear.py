from __future__ import annotations

from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, LinearRegression, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from src.models.base import ModelSpec


def build_linear_pipeline(spec: ModelSpec, numeric_columns: list[str]) -> Pipeline:
    numeric = Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())])
    categorical = Pipeline(
        [
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]
    )
    preprocess = ColumnTransformer(
        [("numeric", numeric, numeric_columns), ("categorical", categorical, ["company_id"])],
        verbose_feature_names_out=True,
    )
    if spec.name in {"linear", "ols"}:
        model = LinearRegression()
    elif spec.name == "ridge":
        model = Ridge(alpha=float(spec.parameters.get("alpha", 1.0)))
    elif spec.name == "elastic_net":
        model = ElasticNet(
            alpha=float(spec.parameters.get("alpha", 0.01)),
            l1_ratio=float(spec.parameters.get("l1_ratio", 0.5)),
            max_iter=int(spec.parameters.get("max_iter", 10_000)),
            random_state=int(spec.parameters.get("random_state", 42)),
        )
    else:
        raise ValueError(f"Unsupported linear model: {spec.name}")
    return Pipeline([("preprocess", preprocess), ("model", model)])
