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
PLACEHOLDER_MARKERS = (
    "<",
    ">",
    "${",
    "REDACTED",
    "CHANGEME",
    "PLACEHOLDER",
    "EXAMPLE",
    "PRIVATE-KEY-VALUE",
    "PRIVATE-SECRET-VALUE",
)


def _assigned_real_value(raw: str) -> bool:
    value = raw.strip().rstrip(",").strip().strip("'\"").strip()
    if not value or value.lower() in {"false", "true", "null", "none"}:
        return False
    if re.fullmatch(r"SecretStr(?:\s*\|\s*None)?\s*=\s*None", value):
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


def scan_git_history(root: Path) -> list[str]:
    """Scan every unique blob reachable from any ref without printing blob contents."""

    listed = subprocess.run(
        ["git", "rev-list", "--objects", "--all"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    paths: dict[str, Path] = {}
    for line in listed.stdout.splitlines():
        object_id, separator, name = line.partition(" ")
        if separator and name:
            paths.setdefault(object_id, Path(name))
    if not paths:
        return []
    batch = subprocess.run(
        ["git", "cat-file", "--batch"],
        cwd=root,
        check=True,
        input=("\n".join(paths) + "\n").encode(),
        capture_output=True,
    ).stdout
    findings: list[str] = []
    cursor = 0
    while cursor < len(batch):
        newline = batch.find(b"\n", cursor)
        if newline < 0:
            break
        header = batch[cursor:newline].decode("ascii", errors="replace").split()
        cursor = newline + 1
        if len(header) != 3 or not header[2].isdigit():
            continue
        object_id, object_type, size_text = header
        size = int(size_text)
        payload = batch[cursor : cursor + size]
        cursor += size + 1
        if object_type != "blob" or object_id not in paths:
            continue
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue
        label = Path(f"history-{object_id[:12]}") / paths[object_id]
        findings.extend(scan_text(label, text))
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
    findings.extend(scan_git_history(root))
    if findings:
        print("\n".join(findings))
        return 1
    print("Tracked-file and reachable-history secret scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
