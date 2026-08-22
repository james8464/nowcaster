from __future__ import annotations

import json
from pathlib import Path

import yaml


def test_readme_is_native_first_and_documents_no_web_runtime():
    root = Path(__file__).resolve().parents[2]
    text = (root / "README.md").read_text(encoding="utf-8")

    assert text.startswith("# Nowcaster for macOS")
    for command in ("make macos-app", "make macos-test", "make demo"):
        assert command in text
    assert "SwiftUI" in text
    assert "WebView" in text
    assert "not investment advice" in text.lower()
    assert "not wall street consensus" in text.lower()
    assert "not affiliated with or endorsed by goldman sachs" in text.lower()


def test_native_documentation_and_visual_evidence_exist():
    root = Path(__file__).resolve().parents[2]
    for path in (
        "docs/architecture.md",
        "docs/backtest_protocol.md",
        "docs/methodology.md",
        "docs/macos_app.md",
        "docs/privacy.md",
        "docs/data_dictionary.md",
        "docs/interview_guide.md",
        "scripts/capture_macos_app.swift",
    ):
        assert (root / path).is_file(), path
    native_images = {path.name for path in (root / "docs" / "images" / "macos").glob("*.png")}
    assert {"today-light.png", "today-dark.png", "backtests-dark-narrow.png"} <= native_images


def test_ci_runs_both_language_suites_and_clean_demo():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    rendered = json.dumps(workflow)
    for command in ("pytest", "swift test", "make demo", "ruff"):
        assert command in rendered


def test_release_builds_a_checksumed_native_archive():
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load((root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8"))
    rendered = json.dumps(workflow)
    for requirement in ("Nowcaster.app", "zip", "shasum -a 256", "upload-artifact"):
        assert requirement in rendered
