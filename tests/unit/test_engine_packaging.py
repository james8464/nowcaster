from __future__ import annotations

import ast
from pathlib import Path


def test_frozen_engine_initializes_multiprocessing_before_cli_dispatch() -> None:
    entrypoint = Path(__file__).resolve().parents[2] / "scripts" / "engine_entry.py"
    module = ast.parse(entrypoint.read_text(encoding="utf-8"))
    guarded = next(
        node
        for node in module.body
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Compare)
        and isinstance(node.test.left, ast.Name)
        and node.test.left.id == "__name__"
    )
    calls: list[str] = []
    for statement in guarded.body:
        if (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Name)
        ):
            calls.append(statement.value.func.id)

    assert calls[:2] == ["freeze_support", "app"]


def test_engine_bundle_declares_dynamic_database_timezone_dependency() -> None:
    build_script = Path(__file__).resolve().parents[2] / "scripts" / "build_engine_bundle.sh"
    source = build_script.read_text(encoding="utf-8")

    assert "--collect-submodules src.deep_research" in source
    assert "--hidden-import pytz" in source
