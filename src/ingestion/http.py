from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import httpx

from src.utils.provenance import canonical_hash


class CachedHttpClient:
    def __init__(self, cache_dir: Path, *, timeout_seconds: float = 30, max_attempts: int = 4):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max_attempts
        self._client = httpx.Client(timeout=timeout_seconds, follow_redirects=True)

    def get_json(
        self,
        url: str,
        *,
        cache_key: str | None = None,
        headers: dict[str, str] | None = None,
        refresh: bool = False,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = cache_key or canonical_hash({"url": url, "params": params})
        path = self.cache_dir / f"{key}.json"
        if path.exists() and not refresh:
            return json.loads(path.read_text(encoding="utf-8"))

        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                response = self._client.get(url, headers=headers, params=params)
                if response.status_code in {429, 500, 502, 503, 504}:
                    retry_after = float(response.headers.get("Retry-After", 2**attempt))
                    time.sleep(min(retry_after, 30))
                    continue
                response.raise_for_status()
                payload = response.json()
                path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
                return payload
            except (httpx.HTTPError, ValueError) as error:
                last_error = error
                if attempt + 1 < self.max_attempts:
                    time.sleep(min(2**attempt, 10))
        raise RuntimeError(f"HTTP request failed after {self.max_attempts} attempts: {url}") from last_error

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> CachedHttpClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class RateLimiter:
    def __init__(self, requests_per_second: float):
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        self.minimum_interval = 1 / requests_per_second
        self.last_request_at = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self.last_request_at
        if elapsed < self.minimum_interval:
            time.sleep(self.minimum_interval - elapsed)
        self.last_request_at = time.monotonic()
