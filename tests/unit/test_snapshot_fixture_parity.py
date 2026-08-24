import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path


def _load_verifier():
    path = Path(__file__).resolve().parents[2] / "scripts" / "verify_snapshot_fixture_parity.py"
    spec = importlib.util.spec_from_file_location("verify_snapshot_fixture_parity", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verifier = _load_verifier()


def _load_synchronizer():
    path = Path(__file__).resolve().parents[2] / "scripts" / "synchronize_snapshot_fixture.py"
    spec = importlib.util.spec_from_file_location("synchronize_snapshot_fixture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_semantic_snapshot_hash_ignores_export_receipt_metadata_only() -> None:
    expected = {
        "schema_version": 2,
        "metadata": {
            "generated_at": "2026-08-24T15:00:00Z",
            "last_refresh": "2026-08-24T14:59:00Z",
            "git_commit": "a" * 40,
            "mode": "demo",
        },
        "pipeline_runs": [
            {
                "pipeline_run_id": "old-id",
                "command": "backtest",
                "started_at": "2026-08-24T14:00:00Z",
                "ended_at": "2026-08-24T14:01:00Z",
                "status": "completed",
            },
            {"pipeline_run_id": "export-old", "command": "export_native_snapshot"},
        ],
        "signals": [{"symbol": "AAPL", "direction": "long"}],
    }
    regenerated = {
        **expected,
        "metadata": {
            "generated_at": "2026-08-24T16:00:00Z",
            "last_refresh": "2026-08-24T15:59:00Z",
            "git_commit": "b" * 40,
            "mode": "demo",
        },
        "pipeline_runs": [
            {
                "pipeline_run_id": "new-id",
                "command": "backtest",
                "started_at": "2026-08-24T16:00:00Z",
                "ended_at": "2026-08-24T16:01:00Z",
                "status": "completed",
            },
            {"pipeline_run_id": "export-new", "command": "export_native_snapshot"},
        ],
    }
    changed = {
        **regenerated,
        "signals": [{"symbol": "AAPL", "direction": "short"}],
    }

    assert verifier.semantic_snapshot_hash(expected) == verifier.semantic_snapshot_hash(regenerated)
    assert verifier.semantic_snapshot_hash(expected) != verifier.semantic_snapshot_hash(changed)


def test_snapshot_sync_preserves_demo_sections_and_replaces_research_sections() -> None:
    synchronizer = _load_synchronizer()
    base = {
        "schema_version": 2,
        "metadata": {"data_mode": "demo"},
        "earnings": [{"id": "legacy"}],
        "strategies": [],
        "ensemble_components": [],
        "dataset_coverage": [],
        "learning_runs": [],
        "causal_audits": [],
    }
    research = {
        "schema_version": 2,
        "metadata": {"data_mode": "ci"},
        "earnings": [],
        "strategies": [{"strategy_id": "causal"}],
        "ensemble_components": [{"strategy_id": f"causal-{index}"} for index in range(12)],
        "dataset_coverage": [{"symbol": "BTCUSDT"}],
        "learning_runs": [{"run_id": "bounded"}],
        "causal_audits": [{"passed": True}],
    }

    merged = synchronizer.merge_research_sections(base, research)

    assert merged["metadata"] == base["metadata"]
    assert merged["earnings"] == base["earnings"]
    for section in set(synchronizer.RESEARCH_SECTIONS) - {"ensemble_components"}:
        assert merged[section] == research[section]
    assert merged["ensemble_components"] == research["ensemble_components"][:10]
    assert base["strategies"] == []


def test_python_research_semantic_mutation_breaks_swift_parity(tmp_path, monkeypatch) -> None:
    synchronizer = _load_synchronizer()
    base = {
        "schema_version": 2,
        "metadata": {"data_mode": "demo"},
        "strategies": [],
        "ensemble_components": [],
        "dataset_coverage": [],
        "learning_runs": [],
        "causal_audits": [],
    }
    python_research = {
        **base,
        "strategies": [{"strategy_id": "causal", "promotion_state": "rejected"}],
        "ensemble_components": [{"strategy_id": "causal"}],
        "dataset_coverage": [{"dataset_hash": "d" * 64}],
        "learning_runs": [{"learning_run_id": "bounded"}],
        "causal_audits": [{"audit_id": "audit", "passed": True}],
    }
    swift = synchronizer.merge_research_sections(base, python_research)
    python_path = tmp_path / "python.json"
    swift_path = tmp_path / "swift.json"
    python_path.write_text(json.dumps(python_research), encoding="utf-8")
    swift_path.write_text(json.dumps(swift), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["verify", "--python-research", str(python_path), "--swift-fixture", str(swift_path)],
    )

    assert verifier.main() == 0

    changed_python = deepcopy(python_research)
    changed_python["strategies"][0]["promotion_state"] = "promoted"
    python_path.write_text(json.dumps(changed_python), encoding="utf-8")

    assert verifier.main() == 1
