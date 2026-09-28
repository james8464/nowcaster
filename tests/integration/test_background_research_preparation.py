"""First opt-in produces a retained manifest from synthetic registered sources."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.background_research_fixtures import learning_fixture

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("entry_mode", ["source", "packaged"])
def test_prepare_cli_preserves_source_and_retries_exact_manifest(tmp_path, entry_mode):
    campaign, protocol, _ = learning_fixture(tmp_path)
    before = {p.name: p.read_bytes() for p in campaign.source_directory.iterdir() if p.is_file()}
    output = tmp_path / "native" / "campaign.json"
    helper = ROOT / "build/engine/dist/nowcaster-engine"
    if entry_mode == "packaged" and not helper.exists():
        pytest.skip("build helper to exercise first-opt-in preparation")
    entry = [str(helper)] if entry_mode == "packaged" else [sys.executable, str(ROOT / "scripts/live_engine_entry.py")]
    command = [
        *entry,
        "strategy",
        "prepare-background-research",
        "--source-directory",
        str(campaign.source_directory),
        "--output",
        str(output),
        "--campaign-id",
        "native-opt-in",
        "--seed",
        "42",
        "--created-at",
        "2026-09-28T00:00:00Z",
    ]
    first = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert first.returncode == 0, first.stderr + first.stdout
    event = json.loads(first.stdout.splitlines()[-1])
    assert event["source_protocol_hash"] == protocol.identity_hash
    retained = output.read_bytes()
    payload = json.loads(retained)
    assert "code_hash" not in payload
    assert payload["schedule"] == protocol.schedule.model_dump(mode="json")
    assert payload["cost_policy"]["minimum_closed_trades"] == protocol.minimum_closed_trades
    assert payload["max_attempts_per_batch"] == 100
    assert payload["max_batches_per_asset_day"] == 1
    assert payload["search_spaces"][0]["strategy_id"] == "desk_donchian_breakout_1m"
    assert payload["search_spaces"][0]["indicators"] == ["close", "donchian_upper", "donchian_lower"]
    retry = subprocess.run(command, capture_output=True, text=True, timeout=60)
    assert retry.returncode == 0, retry.stderr + retry.stdout
    assert output.read_bytes() == retained
    assert before == {p.name: p.read_bytes() for p in campaign.source_directory.iterdir() if p.is_file()}
    if entry_mode == "packaged":
        register = [
            *entry,
            "strategy",
            "register-background-research",
            "--registry-directory",
            str(tmp_path / "registry"),
            "--manifest",
            str(output),
        ]
        registered = subprocess.run(register, capture_output=True, text=True, timeout=90)
        assert registered.returncode == 0, registered.stderr + registered.stdout
        event = json.loads(registered.stdout.splitlines()[-1])
        assert event["status"]["campaign_hash"] == event["campaign_hash"]
        repeated = subprocess.run(
            [
                *register,
                "--expected-campaign-hash",
                event["campaign_hash"],
                "--expected-runtime-code-identity",
                event["runtime_code_identity"],
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert repeated.returncode == 0, repeated.stderr + repeated.stdout
        assert json.loads(repeated.stdout.splitlines()[-1])["campaign_hash"] == event["campaign_hash"]
        assert output.read_bytes() == retained
        assert before == {p.name: p.read_bytes() for p in campaign.source_directory.iterdir() if p.is_file()}


def test_preparation_refuses_changed_manifest_links_and_unknown_family(tmp_path):
    from src.background_research.preparation import prepare_background_research
    from src.research.round_two_registry import register_round

    campaign, protocol, _ = learning_fixture(tmp_path)
    output = tmp_path / "native" / "campaign.json"
    kwargs = dict(
        source_directory=campaign.source_directory,
        output=output,
        campaign_id="native-opt-in",
        seed=42,
        created_at="2026-09-28T00:00:00Z",
    )
    prepare_background_research(**kwargs)
    retained = output.read_bytes()
    with pytest.raises(ValueError, match="immutable"):
        prepare_background_research(**{**kwargs, "seed": 43})
    assert output.read_bytes() == retained
    linked = tmp_path / "linked.json"
    linked.symlink_to(output)
    with pytest.raises(ValueError, match="link"):
        prepare_background_research(**{**kwargs, "output": linked})
    with pytest.raises(ValueError, match="source"):
        prepare_background_research(**{**kwargs, "output": campaign.source_directory / "unsafe.json"})
    unknown = protocol.model_copy(
        update={"candidates": (protocol.candidates[0].model_copy(update={"strategy_id": "unknown"}),)}
    ).validated()
    source = register_round(unknown, tmp_path / "unknown")
    with pytest.raises(ValueError, match="unsupported"):
        prepare_background_research(**{**kwargs, "source_directory": source, "output": tmp_path / "unsupported.json"})


def test_repeat_registration_expected_identity_rejects_before_writing(tmp_path):
    from src.background_research.runtime import register_background_research

    campaign, _, _ = learning_fixture(tmp_path)
    payload = campaign.model_dump(mode="json", exclude={"code_hash"})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(payload))
    registry = tmp_path / "registry"
    registered = register_background_research(registry, manifest)
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="identity"):
        register_background_research(
            registry, manifest, expected_campaign_hash="a" * 64, expected_runtime_code_identity=registered.code_hash
        )
    with pytest.raises(ValueError, match="identity"):
        register_background_research(
            registry, manifest, expected_campaign_hash=registered.identity_hash, expected_runtime_code_identity="a" * 64
        )
    assert before == {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
