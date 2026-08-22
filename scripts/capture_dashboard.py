from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

PAGES = {
    "Overview": "overview.png",
    "Company Research": "company_research.png",
    "Forecast Monitor": "forecast_monitor.png",
    "Model Performance": "model_performance.png",
    "Event Study": "event_study.png",
    "Data Quality": "data_quality.png",
}


def _chrome_executable() -> str | None:
    candidates = [
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    return next((candidate for candidate in candidates if candidate and Path(candidate).exists()), None)


def _wait_for_server(url: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/_stcore/health", timeout=1).text == "ok":
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise TimeoutError("Streamlit did not become healthy")


def capture(database_url: str, output: Path, *, port: int = 8511) -> None:
    output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "NOWCASTER_DATABASE_URL": database_url}
    process = subprocess.Popen(
        [
            str(root / ".venv" / "bin" / "streamlit"),
            "run",
            str(root / "dashboard" / "app.py"),
            "--server.headless",
            "true",
            "--browser.gatherUsageStats",
            "false",
            "--server.port",
            str(port),
        ],
        cwd=root,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_server(base_url)
        executable = _chrome_executable()
        if executable is None:
            raise RuntimeError("Install Google Chrome or Chromium to capture dashboard screenshots")
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, executable_path=executable)
            page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
            page.goto(base_url, wait_until="networkidle")
            for title, filename in PAGES.items():
                page.get_by_text(title, exact=True).first.click()
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(2_500)
                page.screenshot(path=output / filename, full_page=True)
            browser.close()
    finally:
        process.terminate()
        process.wait(timeout=10)


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture all Streamlit dashboard pages.")
    parser.add_argument("--database-url", default="duckdb:///data/nowcaster.duckdb")
    parser.add_argument("--output", type=Path, default=Path("docs/images"))
    parser.add_argument("--port", type=int, default=8511)
    arguments = parser.parse_args()
    capture(arguments.database_url, arguments.output, port=arguments.port)


if __name__ == "__main__":
    main()
