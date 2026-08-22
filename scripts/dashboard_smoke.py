from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    command = [
        str(root / ".venv" / "bin" / "streamlit"),
        "run",
        str(root / "dashboard" / "app.py"),
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
        "--server.port",
        "8511",
    ]
    process = subprocess.Popen(
        command,
        cwd=root,
        env={**os.environ, "NOWCASTER_DATABASE_URL": f"duckdb:///{root / 'data' / 'nowcaster.duckdb'}"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                response = httpx.get("http://127.0.0.1:8511/_stcore/health", timeout=1)
                if response.status_code == 200 and response.text == "ok":
                    print("Streamlit health check: ok")
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        raise TimeoutError("Streamlit did not become healthy within 30 seconds")
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


if __name__ == "__main__":
    main()
