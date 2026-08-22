from __future__ import annotations

from pathlib import Path

import nbformat
from nbconvert.preprocessors import ExecutePreprocessor


def test_research_notebooks_execute_without_reimplementing_pipeline_logic(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("NOWCASTER_DATABASE_URL", f"duckdb:///{tmp_path / 'notebooks.duckdb'}")
    for path in sorted((root / "notebooks").glob("*.ipynb")):
        notebook = nbformat.read(path, as_version=4)
        ExecutePreprocessor(timeout=60, kernel_name="python3").preprocess(notebook, {"metadata": {"path": str(root)}})
        source = "\n".join(cell.source for cell in notebook.cells if cell.cell_type == "code")
        assert "train_test_split" not in source
        assert "def build_" not in source

    assert len(list((root / "notebooks").glob("*.ipynb"))) == 3
