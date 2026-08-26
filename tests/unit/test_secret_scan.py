from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


def _load_scanner():
    path = Path(__file__).resolve().parents[2] / "scripts" / "scan_tracked_secrets.py"
    spec = importlib.util.spec_from_file_location("scan_tracked_secrets", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


scanner = _load_scanner()


def test_provider_credential_assignments_are_detected_without_echoing_values() -> None:
    alpaca_name = "APCA_" + "API_KEY_ID"
    alpaca_value = "PK" + "TESTONLY123456789012345678"
    binance_name = "BINANCE_" + "API_SECRET"
    binance_value = "B" * 64
    text = f"export {alpaca_name}={alpaca_value}\n{binance_name}: '{binance_value}'\n"

    findings = scanner.scan_text(Path("config/local.env"), text)
    rendered = "\n".join(findings)

    assert len(findings) == 2
    assert "possible Alpaca credential assignment" in rendered
    assert "possible Binance credential assignment" in rendered
    assert alpaca_value not in rendered
    assert binance_value not in rendered


def test_provider_secret_scan_ignores_names_placeholders_and_presence_metadata() -> None:
    alpaca_name = "ALPACA_" + "API_SECRET"
    binance_name = "BINANCE_" + "API_KEY"
    text = "\n".join(
        [
            f'os.getenv("{alpaca_name}")',
            f"export {alpaca_name}='<local-secret>'",
            f'"{binance_name.lower()}_present": false',
            "alpaca_secret: SecretStr | None = None",
            '"alpaca_key_id": "private-key-value"',
            "The Binance API key stays outside Git.",
        ]
    )

    assert scanner.scan_text(Path("docs/provider-example.md"), text) == []


def test_provider_shaped_unassigned_identifiers_do_not_trigger_false_positive() -> None:
    text = "strategy hash: " + "a" * 64 + "\nfixture id: PKTESTONLY123456789012345678\n"

    assert scanner.scan_text(Path("data/research-summary.json"), text) == []


def test_secretstr_constructor_is_not_a_scanner_bypass() -> None:
    secret_name = "ALPACA_" + "API_SECRET"
    findings = scanner.scan_text(
        Path("src/example.py"),
        f'{secret_name}: SecretStr("embedded-real-value")\n',
    )

    assert len(findings) == 1


def test_history_scan_finds_a_secret_committed_then_deleted_without_echoing_it(tmp_path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    secret_name = "BINANCE_" + "API_SECRET"
    secret_value = "Q" * 64
    path = tmp_path / "local.env"
    path.write_text(f"{secret_name}={secret_value}\n", encoding="utf-8")
    subprocess.run(["git", "add", "local.env"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "add"], cwd=tmp_path, check=True)
    path.unlink()
    subprocess.run(["git", "add", "-u"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "remove"], cwd=tmp_path, check=True)

    findings = scanner.scan_git_history(tmp_path)
    rendered = "\n".join(findings)

    assert "possible Binance credential assignment" in rendered
    assert secret_value not in rendered
