from __future__ import annotations

import pandas as pd
from sklearn.inspection import permutation_importance


def permutation_importance_frame(model, X: pd.DataFrame, y: pd.Series, *, seed: int = 42) -> pd.DataFrame:
    result = permutation_importance(model, X, y, n_repeats=20, random_state=seed, scoring="neg_mean_absolute_error")
    return pd.DataFrame(
        {"feature": X.columns, "importance": result.importances_mean, "importance_std": result.importances_std}
    ).sort_values("importance", ascending=False)
