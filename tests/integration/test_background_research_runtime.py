from pathlib import Path

import pytest

from src.background_research import runtime
from src.background_research.registry import LearningRegistry
from src.background_research.training import LearningTrainer
from src.deep_research.control import ResearchControl
from tests.background_research_fixtures import learning_fixture


def test_runtime_rejects_library_fixture_identity_before_dispatch(tmp_path):
    campaign, _, _ = learning_fixture(tmp_path)
    registry = LearningRegistry(tmp_path / "registry")
    registry.register(campaign)
    control = ResearchControl(tmp_path / "control", run_id="identity", nonce="n" * 32)
    control.initialize()
    with pytest.raises(ValueError, match="runtime.*identity"):
        runtime.BackgroundLearningRunner(registry, LearningTrainer(registry)).run(
            campaign.identity_hash, control=control, emit=lambda _: None
        )
    assert registry.read_status(campaign.identity_hash).attempt_count == 0


def test_source_runtime_identity_detects_uncommitted_code_and_environment_change(tmp_path, monkeypatch):
    (tmp_path / "src").mkdir()
    module = tmp_path / "src/example.py"
    module.write_text("VALUE = 1\n")
    first = runtime.runtime_code_identity(source_root=tmp_path)
    module.write_text("VALUE = 2\n")
    assert runtime.runtime_code_identity(source_root=tmp_path) != first
    second = runtime.runtime_code_identity(source_root=tmp_path)
    monkeypatch.setattr(runtime, "environment_identity", lambda: {"changed": "environment"})
    assert runtime.runtime_code_identity(source_root=tmp_path) != second


def test_background_environment_drops_provider_credentials(monkeypatch):
    import os

    monkeypatch.setattr(os, "environ", os.environ.copy())
    monkeypatch.setenv("ALPACA_API_KEY", "must-not-inherit")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "must-not-inherit")
    monkeypatch.setenv("UNRECOGNIZED_PROVIDER_TOKEN", "must-not-inherit")
    monkeypatch.setenv("_PYI_UNRECOGNIZED_PROVIDER_TOKEN", "must-not-inherit")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    runtime.restrict_background_environment()
    import os

    assert "ALPACA_API_KEY" not in os.environ
    assert "AWS_SECRET_ACCESS_KEY" not in os.environ
    assert "UNRECOGNIZED_PROVIDER_TOKEN" not in os.environ
    assert "_PYI_UNRECOGNIZED_PROVIDER_TOKEN" not in os.environ
    assert os.environ["PATH"] == "/usr/bin:/bin"


def test_environment_digest_hashes_actual_distribution_artifacts(tmp_path):
    from scripts.engine_manifest import distribution_identity

    artifact = tmp_path / "library.py"
    artifact.write_text("value = 1\n")

    class SyntheticDistribution:
        version = "1.0"
        files = [Path("library.py")]

        def locate_file(self, path):
            return tmp_path / path

    original = distribution_identity(SyntheticDistribution())
    artifact.write_text("value = 2\n")
    assert distribution_identity(SyntheticDistribution()) != original


def test_manual_worker_limit_reserves_host_cores_without_changing_campaign(tmp_path, monkeypatch):
    monkeypatch.setattr("src.background_research.training.os.cpu_count", lambda: 8)
    registry = LearningRegistry(tmp_path / "registry")
    assert LearningTrainer(registry).workers == 4
    assert LearningTrainer(registry, workers=2).workers == 2
    with pytest.raises(ValueError, match="workers"):
        LearningTrainer(registry, workers=7)
    with pytest.raises(ValueError, match="workers"):
        LearningTrainer(registry, workers=0)
