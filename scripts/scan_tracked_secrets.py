from __future__ import annotations

import re
import subprocess
from pathlib import Path

PATTERNS = {
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "OpenAI-style secret": re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}
PROVIDER_ASSIGNMENT = re.compile(
    r"""(?ix)
    ["']?
    (?P<name>(?:APCA|ALPACA|BINANCE)_[A-Z0-9_]*(?:KEY|SECRET|TOKEN)[A-Z0-9_]*)
    ["']?
    \s*(?:=|:)\s*
    (?P<value>.+?)\s*$
    """
)
PLACEHOLDER_MARKERS = ("<", ">", "${", "REDACTED", "CHANGEME", "PLACEHOLDER", "EXAMPLE")


def _assigned_real_value(raw: str) -> bool:
    value = raw.strip().rstrip(",").strip().strip("'\"").strip()
    if not value or value.lower() in {"false", "true", "null", "none"}:
        return False
    upper = value.upper()
    return not any(marker in upper for marker in PLACEHOLDER_MARKERS)


def scan_text(relative: Path, text: str) -> list[str]:
    findings: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        assignment = PROVIDER_ASSIGNMENT.search(line)
        if assignment and _assigned_real_value(assignment.group("value")):
            provider = "Binance" if assignment.group("name").upper().startswith("BINANCE_") else "Alpaca"
            findings.append(f"{relative}:{line_number}: possible {provider} credential assignment")
        for label, pattern in PATTERNS.items():
            if pattern.search(line):
                findings.append(f"{relative}:{line_number}: possible {label}")
    return findings


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True)
    findings: list[str] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        relative = Path(raw.decode())
        path = root / relative
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(scan_text(relative, text))
    if findings:
        print("\n".join(findings))
        return 1
    print("Tracked-file secret scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
