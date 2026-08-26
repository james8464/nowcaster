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

    assert "--collect-submodules src.deep_research" not in source
    assert "--hidden-import pytz" in source
    assert "--collect-submodules src.live_monitor" in source
    assert "--hidden-import websockets.asyncio.client" in source
    assert "--exclude-module pytest" in source
    assert "--exclude-module matplotlib" in source
    assert '"$PROJECT_ROOT/scripts/live_engine_entry.py"' in source


def test_live_monitor_transport_has_no_broker_mutation_imports() -> None:
    root = Path(__file__).resolve().parents[2]
    sources = "\n".join(path.read_text(encoding="utf-8") for path in (root / "src/live_monitor").glob("*.py"))

    assert "submit_order" not in sources
    assert "cancel_order" not in sources
    assert "src.trading" not in sources


def test_frozen_helper_declares_only_the_required_pyinstaller_library_entitlement() -> None:
    root = Path(__file__).resolve().parents[2]
    entitlements = (root / "macos/Nowcaster/Resources/Engine.entitlements").read_text(encoding="utf-8")
    build = (root / "scripts/build_macos_app.sh").read_text(encoding="utf-8")

    assert "com.apple.security.cs.disable-library-validation" in entitlements
    assert "com.apple.security.get-task-allow" not in entitlements
    assert '--entitlements "$PACKAGE_ROOT/Resources/Engine.entitlements"' in build
