from __future__ import annotations

import hashlib
import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pandas as pd
import pytest

from src.ingestion.binance_archive import BinancePublicArchive
from src.strategies.types import BarInterval


def _archive(rows: list[list[object]], name: str) -> bytes:
    payload = "\n".join(",".join(str(value) for value in row) for row in rows).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name.replace(".zip", ".csv"), payload)
    return buffer.getvalue()


def _row(open_time: int, close_time: int) -> list[object]:
    return [open_time, "100", "101", "99", "100.5", "12", close_time, "1200", 20, "6", "600", "0"]


def test_archive_download_verifies_checksum_and_parses_millisecond_and_microsecond_timestamps(tmp_path: Path) -> None:
    names = {
        "BTCUSDT-5m-2024-12.zip": _archive(
            [_row(1735689000000, 1735688000000), _row(1735689300000, 1735689599999)],
            "BTCUSDT-5m-2024-12.zip",
        ),
        "BTCUSDT-5m-2025-01.zip": _archive([_row(1735689600000000, 1735689899999999)], "BTCUSDT-5m-2025-01.zip"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1].replace(".CHECKSUM", "")
        if name not in names:
            return httpx.Response(404)
        payload = names[name]
        if request.url.path.endswith(".CHECKSUM"):
            checksum = hashlib.sha256(payload).hexdigest()
            return httpx.Response(200, content=f"{checksum}  {name}\n".encode())
        return httpx.Response(200, content=payload)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = BinancePublicArchive(client, cache_dir=tmp_path).fetch(
            symbol="BTCUSDT",
            interval=BarInterval.FIVE_MINUTES,
            start=datetime(2024, 12, 1, tzinfo=UTC),
            end=datetime(2025, 2, 1, tzinfo=UTC),
        )

    assert len(result.bars) == 2
    assert list(result.bars["open_timestamp"]) == [
        pd.Timestamp("2024-12-31T23:55:00Z"),
        pd.Timestamp("2025-01-01T00:00:00Z"),
    ]
    assert list(result.bars["close_timestamp"]) == [
        pd.Timestamp("2025-01-01T00:00:00Z"),
        pd.Timestamp("2025-01-01T00:05:00Z"),
    ]
    assert result.bars["available_at"].equals(result.bars["close_timestamp"])
    assert all(item["checksum_verified"] for item in result.manifest)
    assert sum(item["invalid_boundary_rows"] for item in result.manifest) == 1
    assert result.evidence_tier == "retrospective_archive_only"
    assert result.eligible_for_live_promotion is False


def test_archive_rejects_checksum_mismatch_before_parsing(tmp_path: Path) -> None:
    payload = _archive([_row(1735689600000, 1735689899999)], "BTCUSDT-5m-2025-01.zip")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(".CHECKSUM"):
            return httpx.Response(200, content=f"{'0' * 64}  BTCUSDT-5m-2025-01.zip\n".encode())
        return httpx.Response(200, content=payload)

    with (
        httpx.Client(transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ValueError, match="checksum"),
    ):
        BinancePublicArchive(client, cache_dir=tmp_path).fetch(
            symbol="BTCUSDT",
            interval=BarInterval.FIVE_MINUTES,
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 2, 1, tzinfo=UTC),
        )


def test_archive_falls_back_to_daily_files_when_a_complete_month_is_not_published(tmp_path: Path) -> None:
    daily_name = "BTCUSDT-5m-2026-08-01.zip"
    payload = _archive([_row(1785542400000000, 1785542699999999)], daily_name)

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        if "2026-08.zip" in name:
            return httpx.Response(404)
        if name.replace(".CHECKSUM", "") == daily_name:
            if name.endswith(".CHECKSUM"):
                return httpx.Response(200, content=f"{hashlib.sha256(payload).hexdigest()}  {daily_name}\n".encode())
            return httpx.Response(200, content=payload)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = BinancePublicArchive(client, cache_dir=tmp_path).fetch(
            symbol="BTCUSDT",
            interval=BarInterval.FIVE_MINUTES,
            start=datetime(2026, 8, 1, tzinfo=UTC),
            end=datetime(2026, 8, 2, tzinfo=UTC),
        )

    assert len(result.bars) == 1
    assert result.manifest[0]["frequency"] == "daily"
    assert result.unavailable == ()


def test_archive_rejects_path_unsafe_symbol_before_network_or_cache_access(tmp_path: Path) -> None:
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(ValueError, match="symbol"):
        BinancePublicArchive(client, cache_dir=tmp_path).fetch(
            symbol="../../escape",
            interval=BarInterval.FIVE_MINUTES,
            start=datetime(2025, 1, 1, tzinfo=UTC),
            end=datetime(2025, 2, 1, tzinfo=UTC),
        )

    assert requests == 0
    assert not list(tmp_path.rglob("*"))
