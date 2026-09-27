from __future__ import annotations

import ast
import plistlib
import subprocess
import sys
import tomllib
from pathlib import Path

from packaging.requirements import Requirement


def test_database_dependency_excludes_the_reproduced_reflection_regression() -> None:
    root = Path(__file__).resolve().parents[2]
    dependencies = tomllib.loads((root / "pyproject.toml").read_text())["project"]["dependencies"]
    requirement = next(Requirement(item) for item in dependencies if Requirement(item).name == "sqlalchemy")
    assert requirement.specifier.contains("2.0.52")
    assert not requirement.specifier.contains("2.1.1")
    assert not requirement.specifier.contains("2.0.45")


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


def test_live_monitor_import_does_not_require_research_only_scipy_or_sklearn() -> None:
    root = Path(__file__).resolve().parents[2]
    probe = """
import importlib.abc
import sys

class ResearchDependencyBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition(".")[0] in {"scipy", "sklearn"}:
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, ResearchDependencyBlocker())
import src.live_monitor.command
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_frozen_helper_entitlements_allow_library_loading_but_not_debugging() -> None:
    root = Path(__file__).resolve().parents[2]
    entitlements = plistlib.loads((root / "macos/Nowcaster/Resources/Engine.entitlements").read_bytes())
    assert entitlements == {"com.apple.security.cs.disable-library-validation": True}


def test_xcode_signing_allows_local_development_identity_without_tracking_it() -> None:
    root = Path(__file__).resolve().parents[2]
    config = (root / "macos/Nowcaster/Resources/Signing.xcconfig").read_text()
    project = (root / "macos/Nowcaster/Nowcaster.xcodeproj/project.pbxproj").read_text()
    assert "CODE_SIGN_IDENTITY = -" in config
    assert '#include? "Signing.local.xcconfig"' in config
    # Debug/Release for the application and its native UI test runner.
    assert project.count("baseConfigurationReference = A01C00000000000000000011") == 4
    assert "CODE_SIGN_IDENTITY =" not in project  # Must not override the local config.
    assert "DEVELOPMENT_TEAM =" not in project
    assert "macos/Nowcaster/Resources/Signing.local.xcconfig" in (root / ".gitignore").read_text()


def test_embedded_helpers_inherit_xcode_resolved_signing_identity() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "scripts/embed_macos_runtime.sh").read_text()
    assert "IDENTITY=${NOWCASTER_CODESIGN_IDENTITY:-${EXPANDED_CODE_SIGN_IDENTITY:--}}" in source


def test_runtime_manifest_is_an_explicit_xcode_output_for_incremental_signing() -> None:
    root = Path(__file__).resolve().parents[2]
    project = (root / "macos/Nowcaster/Nowcaster.xcodeproj/project.pbxproj").read_text()
    assert '"$(TARGET_BUILD_DIR)/$(UNLOCALIZED_RESOURCES_FOLDER_PATH)/engine-manifest.json"' in project
    assert "alwaysOutOfDate = 1;" in project  # Python changes still rebuild the embedded runtime.


def test_packagers_do_not_clean_other_projects_shared_pyinstaller_cache() -> None:
    root = Path(__file__).resolve().parents[2]
    for script in ["build_engine_bundle.sh", "build_paper_signals_bundle.sh"]:
        source = (root / "scripts" / script).read_text()
        assert 'export PYINSTALLER_CONFIG_DIR="$PROJECT_ROOT/build/pyinstaller-cache"' in source
