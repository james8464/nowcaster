"""Bounded external read-only process observer for sandboxed native XCTest.

Never launches/stops the app or a worker. The only controlled child is an
unrelated sleep sentinel used to prove app Quit does not kill unrelated work.
Run in a new marked UIAcceptanceFixtures directory; retain observations there.
"""

import argparse
import json
import signal
import subprocess
import time
from pathlib import Path


def scoped_processes(output: str) -> list[str]:
    return [
        row
        for row in output.splitlines()
        if "/Applications/Nowcaster.app/Contents/Helpers/" in row
        or "/Applications/Nowcaster.app/Contents/MacOS/Nowcaster" in row
    ]


def read_evidence(source: Path, registry: Path) -> dict:
    evidence = {}
    for key, path in {
        "collector": source / "live-paper-signal-state.json",
        "provider": source / "live-paper-provider-health.json",
        "research": registry / "status.json",
    }.items():
        try:
            evidence[key] = json.loads(path.read_text())
        except (OSError, ValueError) as error:
            evidence[key] = {"observation_error": type(error).__name__}
    evidence["controls"] = []
    for path in sorted((registry / "controls").glob("*.control.json")):
        try:
            value = json.loads(path.read_text())
            evidence["controls"].append({key: value.get(key) for key in ("run_id", "state", "updated_at")})
        except (OSError, ValueError) as error:
            evidence["controls"].append({"observation_error": type(error).__name__})
    return evidence


def observe(root: Path, duration: int, source: Path | None = None, registry: Path | None = None) -> None:
    root = root.resolve()
    if "UIAcceptanceFixtures" not in root.parts or root.exists() or not 1 <= duration <= 3600:
        raise ValueError("Use a new UIAcceptanceFixtures directory and a bounded duration")
    root.mkdir(parents=True)
    (root / "UI-TEST-ONLY.md").write_text("Read-only installed-app process observation; unrelated sleep sentinel.\n")
    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    sentinel = subprocess.Popen(["/bin/sleep", str(duration + 10)])
    deadline = time.monotonic() + duration
    try:
        with (root / "samples.jsonl").open("x") as history:
            while running and time.monotonic() < deadline:
                result = subprocess.run(
                    ["/bin/ps", "-wwaxo", "pid=,ppid=,lstart=,command="],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                sample = {
                    "observed_at": time.time(),
                    "processes": scoped_processes(result.stdout),
                    "unrelated_pid": sentinel.pid,
                    "unrelated_alive": sentinel.poll() is None,
                }
                if source is not None and registry is not None:
                    sample["evidence"] = read_evidence(source, registry)
                encoded = json.dumps(sample) + "\n"
                temporary = root / "snapshot.pending"
                temporary.write_text(encoded)
                temporary.replace(root / "snapshot.json")
                history.write(encoded)
                history.flush()
                time.sleep(0.5)
    finally:
        if sentinel.poll() is None:
            sentinel.terminate()
        sentinel.wait(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--seconds", type=int, default=1200)
    parser.add_argument("--source-directory", type=Path)
    parser.add_argument("--registry-directory", type=Path)
    arguments = parser.parse_args()
    observe(arguments.root, arguments.seconds, arguments.source_directory, arguments.registry_directory)
