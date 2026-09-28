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


def test_reused_helper_refuses_stale_source_instead_of_relabeling_binary(tmp_path):
    import json
    import shutil

    from scripts.engine_manifest import build_manifest

    root = Path(__file__).resolve().parents[2]
    (tmp_path / "scripts").mkdir()
    for name in ("build_engine_bundle.sh", "engine_manifest.py"):
        shutil.copy(root / "scripts" / name, tmp_path / "scripts" / name)
    (tmp_path / "src").mkdir()
    module = tmp_path / "src/probe.py"
    module.write_text("VALUE = 1\n")
    dist = tmp_path / "build/engine/dist"
    dist.mkdir(parents=True)
    binary = dist / "nowcaster-engine"
    binary.write_bytes(b"synthetic stale executable")
    binary.chmod(0o755)
    manifest = dist / "engine-manifest.json"
    manifest.write_text(json.dumps(build_manifest(tmp_path, binary)))
    original = manifest.read_bytes()
    module.write_text("VALUE = 2\n")
    import os

    result = subprocess.run(
        ["zsh", str(tmp_path / "scripts/build_engine_bundle.sh")],
        env={**os.environ, "NOWCASTER_BUILD_PYTHON": sys.executable, "NOWCASTER_REUSE_ENGINE_BUNDLE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode != 0
    assert manifest.read_bytes() == original


def test_registration_runtime_does_not_import_scipy_or_sklearn_before_ownership(tmp_path):
    import json

    from tests.background_research_fixtures import learning_fixture

    campaign, _, _ = learning_fixture(tmp_path)
    manifest = tmp_path / "synthetic-registration.json"
    payload = campaign.model_dump(mode="json")
    payload.pop("code_hash")
    manifest.write_text(json.dumps(payload))
    root = Path(__file__).resolve().parents[2]
    probe = """
import importlib.abc
import sys
class HeavyImportBlocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.partition('.')[0] in {'scipy', 'sklearn'}:
            raise RuntimeError('heavy dependency before ownership: ' + fullname)
sys.meta_path.insert(0, HeavyImportBlocker())
import src.background_research.runtime as runtime
runtime.restrict_background_environment()
from pathlib import Path
campaign = runtime.register_background_research(Path(sys.argv[1]), Path(sys.argv[2]))
assert campaign.campaign_id == 'learning'
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(tmp_path / "registry"), str(manifest)],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def test_legacy_research_and_learning_exports_retain_import_identity():
    import src.learning as learning
    import src.research as research
    from src.learning.search import discover_rules
    from src.research.full_history import run_full_strategy_research

    assert learning.discover_rules is discover_rules
    assert research.run_full_strategy_research is run_full_strategy_research
    for facade in (learning, research):
        for name in facade.__all__:
            assert getattr(facade, name) is getattr(facade, name)
