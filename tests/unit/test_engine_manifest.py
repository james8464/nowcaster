from __future__ import annotations

import json

from scripts.engine_manifest import build_manifest, verify_manifest


def test_engine_manifest_is_deterministic_and_detects_source_or_binary_mutation(tmp_path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "config").mkdir()
    (tmp_path / "src" / "module.py").write_text("VALUE = 1\n")
    (tmp_path / "config" / "trading.yaml").write_text("live_enabled: false\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n")
    (tmp_path / ".env").write_text("SECRET=excluded\n")
    executable = tmp_path / "nowcaster-engine"
    executable.write_bytes(b"engine")
    first = build_manifest(tmp_path, executable)
    assert first == build_manifest(tmp_path, executable)
    assert ".env" not in json.dumps(first)
    assert verify_manifest(tmp_path, executable, first)
    (tmp_path / "src" / "module.py").write_text("VALUE = 2\n")
    assert not verify_manifest(tmp_path, executable, first)
