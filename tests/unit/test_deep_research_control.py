from __future__ import annotations

import json
import stat

import pytest

from src.deep_research.control import ControlState, ResearchControl


def test_control_file_is_private_owned_and_allows_safe_transitions(tmp_path) -> None:
    control = ResearchControl(tmp_path, run_id="run-1", nonce="n" * 32)
    control.initialize()

    assert control.read() is ControlState.RUNNING
    assert stat.S_IMODE(control.path.stat().st_mode) == 0o600
    control.request(ControlState.PAUSED)
    assert control.read() is ControlState.PAUSED
    control.request(ControlState.RUNNING)
    assert control.read() is ControlState.RUNNING
    control.request(ControlState.STOPPED)
    assert control.read() is ControlState.STOPPED

    with pytest.raises(ValueError, match="terminal"):
        control.request(ControlState.RUNNING)


def test_control_rejects_tampered_identity_and_unknown_commands(tmp_path) -> None:
    control = ResearchControl(tmp_path, run_id="run-1", nonce="n" * 32)
    control.initialize()
    payload = json.loads(control.path.read_text(encoding="utf-8"))
    payload["nonce"] = "attacker"
    control.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="identity"):
        control.read()

    control.initialize()
    payload = json.loads(control.path.read_text(encoding="utf-8"))
    payload["state"] = "flatten_account"
    control.path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="state"):
        control.read()
