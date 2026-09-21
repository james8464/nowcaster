"""Release-only verification of the standalone, source-independent collector."""

import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from src.research.round_two_contracts import ResearchRoundProtocol
from src.research.round_two_registry import register_round


@pytest.mark.skipif(not os.environ.get("NOWCASTER_PAPER_HELPER"), reason="requires a built release helper")
def test_packaged_helper_reads_registered_state_without_python_or_checkout(tmp_path):
    helper = Path(os.environ["NOWCASTER_PAPER_HELPER"]).resolve()
    protocol = ResearchRoundProtocol.default(round_id="bundle-verification", starts_at=datetime.now(UTC))
    directory = tmp_path / "evidence"
    register_round(protocol, directory)
    retained = (directory / "protocol.json").read_bytes()
    started = time.monotonic()
    result = subprocess.run(
        [str(helper), "status", "--directory", str(directory)],
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "TMPDIR": str(tmp_path)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert time.monotonic() - started < 15, "helper must respond within the native request deadline"
    state = json.loads(result.stdout)
    assert state["kind"] == "stopped"
    assert state["protocol_hash"] == protocol.identity_hash
    assert state["suggestion"] is None
    assert (directory / "protocol.json").read_bytes() == retained
    assert sorted(path.name for path in directory.iterdir()) == ["protocol.json"]

    # The causal evaluator binds exact source bytes and its static registry.
    # These are runtime inputs, not a dependency on the original checkout.
    resources = helper.parents[1] / "Resources"
    source = Path(__file__).resolve().parents[2]
    required = [Path("config/strategies.yaml"), Path("src/research/round_two_walkforward.py")]
    required.extend(path.relative_to(source) for path in (source / "src/strategies").rglob("*.py"))
    for relative in required:
        assert (resources / relative).read_bytes() == (source / relative).read_bytes()
