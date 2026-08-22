from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def project_root(tmp_path: Path) -> Path:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "model.yaml").write_text(
        "forecast_horizons: [30, 14, 7, 1]\nminimum_training_quarters: 8\nrandom_seed: 42\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "universe.yaml").write_text(
        "companies:\n  - ticker: SBUX\n    cik: '829224'\n    name: Starbucks Corporation\n    enabled: true\n",
        encoding="utf-8",
    )
    (tmp_path / "config" / "features.yaml").write_text(
        "availability_lags:\n  wikipedia: 1\n",
        encoding="utf-8",
    )
    return tmp_path
