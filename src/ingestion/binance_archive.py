"""Checksum-verified Binance public archives for retrospective research only."""

from __future__ import annotations

import hashlib
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd

from src.ingestion.bars import INTERVAL_DURATION, atomic_write_bytes, require_utc
from src.strategies.types import BarInterval

_BASE_URL = "https://data.binance.vision/data/spot"
_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "ignore",
)
_OUTPUT_COLUMNS = (
    "provider",
    "feed",
    "symbol",
    "interval",
    "open_timestamp",
    "close_timestamp",
    "available_at",
    "revision",
    "finalized",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trade_count",
    "taker_buy_base_volume",
    "taker_buy_quote_volume",
    "archive_name",
    "archive_sha256",
)
_CHECKSUM = re.compile(r"^([0-9a-fA-F]{64})(?:\s+\*?([^\s]+))?")
_SYMBOL = re.compile(r"^[A-Z0-9]{2,30}$")


@dataclass(frozen=True, slots=True)
class BinanceArchiveResult:
    bars: pd.DataFrame
    manifest: tuple[dict[str, Any], ...]
    unavailable: tuple[str, ...]
    evidence_tier: str = "retrospective_archive_only"
    eligible_for_live_promotion: bool = False


def _month_start(value: datetime) -> datetime:
    return datetime(value.year, value.month, 1, tzinfo=UTC)


def _next_month(value: datetime) -> datetime:
    return datetime(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1, tzinfo=UTC)


def _days(start: datetime, end: datetime) -> list[date]:
    cursor = start.date()
    result: list[date] = []
    while datetime(cursor.year, cursor.month, cursor.day, tzinfo=UTC) < end:
        result.append(cursor)
        cursor += timedelta(days=1)
    return result


def _timestamp(values: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(values, errors="raise").astype("int64")
    microseconds = numeric.abs() >= 100_000_000_000_000
    if microseconds.all():
        return pd.to_datetime(numeric, unit="us", utc=True)
    if (~microseconds).all():
        return pd.to_datetime(numeric, unit="ms", utc=True)
    result = pd.Series(pd.NaT, index=numeric.index, dtype="datetime64[ns, UTC]")
    result.loc[microseconds] = pd.to_datetime(numeric.loc[microseconds], unit="us", utc=True)
    result.loc[~microseconds] = pd.to_datetime(numeric.loc[~microseconds], unit="ms", utc=True)
    return result


class BinancePublicArchive:
    """Read official ZIP/checksum pairs without treating them as point-in-time vintages."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        cache_dir: Path,
        base_url: str = _BASE_URL,
        maximum_uncompressed_bytes: int = 256 * 1024 * 1024,
    ):
        self.client = client
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.base_url = base_url.rstrip("/")
        self.maximum_uncompressed_bytes = maximum_uncompressed_bytes
        if maximum_uncompressed_bytes < 1:
            raise ValueError("archive size bound must be positive")

    def _paths(self, frequency: str, symbol: str, interval: BarInterval, name: str) -> tuple[Path, Path]:
        parent = self.cache_dir / "binance-public-data" / "spot" / frequency / "klines" / symbol / interval.value
        return parent / name, parent / f"{name}.CHECKSUM"

    def _request(self, url: str) -> bytes | None:
        response = self.client.get(url)
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    @staticmethod
    def _expected_checksum(payload: bytes, name: str) -> str:
        line = payload.decode("ascii").strip().splitlines()[0]
        match = _CHECKSUM.match(line)
        if match is None:
            raise ValueError(f"invalid Binance archive checksum for {name}")
        checksum, recorded_name = match.groups()
        if recorded_name is not None and Path(recorded_name).name != name:
            raise ValueError(f"Binance archive checksum filename mismatch for {name}")
        return checksum.lower()

    def _payload(self, frequency: str, symbol: str, interval: BarInterval, name: str) -> bytes | None:
        archive_path, checksum_path = self._paths(frequency, symbol, interval, name)
        if archive_path.exists() and checksum_path.exists():
            archive = archive_path.read_bytes()
            checksum = checksum_path.read_bytes()
        else:
            parent_url = f"{self.base_url}/{frequency}/klines/{symbol}/{interval.value}"
            checksum = self._request(f"{parent_url}/{name}.CHECKSUM")
            if checksum is None:
                return None
            archive = self._request(f"{parent_url}/{name}")
            if archive is None:
                return None
        expected = self._expected_checksum(checksum, name)
        observed = hashlib.sha256(archive).hexdigest()
        if observed != expected:
            raise ValueError(f"Binance archive checksum mismatch for {name}")
        if not archive_path.exists() or not checksum_path.exists():
            atomic_write_bytes(archive_path, archive)
            atomic_write_bytes(checksum_path, checksum)
        return archive

    def _parse(
        self,
        payload: bytes,
        *,
        name: str,
        checksum: str,
        symbol: str,
        interval: BarInterval,
        start: datetime,
        end: datetime,
    ) -> pd.DataFrame:
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                members = [item for item in archive.infolist() if not item.is_dir()]
                if (
                    len(members) != 1
                    or not members[0].filename.endswith(".csv")
                    or Path(members[0].filename).name != members[0].filename
                    or members[0].file_size > self.maximum_uncompressed_bytes
                ):
                    raise ValueError(f"unsafe or malformed Binance archive: {name}")
                raw = archive.read(members[0])
        except zipfile.BadZipFile as error:
            raise ValueError(f"malformed Binance ZIP archive: {name}") from error
        frame = pd.read_csv(io.BytesIO(raw), header=None, names=_COLUMNS)
        if frame.empty:
            return pd.DataFrame(columns=_OUTPUT_COLUMNS)
        if frame.shape[1] != len(_COLUMNS):
            raise ValueError(f"Binance archive has an unexpected column count: {name}")
        opened = _timestamp(frame["open_time"])
        raw_close = pd.to_numeric(frame["close_time"], errors="raise").astype("int64")
        closed = _timestamp(raw_close) + pd.to_timedelta(
            (raw_close.abs() < 100_000_000_000_000).astype(int) * 999 + 1,
            unit="us",
        )
        # Millisecond archives end at .999; microsecond archives end at .999999.
        microsecond_rows = raw_close.abs() >= 100_000_000_000_000
        closed.loc[microsecond_rows] = _timestamp(raw_close.loc[microsecond_rows]) + pd.Timedelta(microseconds=1)
        in_requested_range = (opened >= pd.Timestamp(start)) & (opened < pd.Timestamp(end))
        valid_boundary = closed == opened + INTERVAL_DURATION[interval]
        invalid_boundary_rows = int((in_requested_range & ~valid_boundary).sum())
        selected = frame.loc[in_requested_range & valid_boundary].copy()
        if selected.empty:
            result = pd.DataFrame(columns=_OUTPUT_COLUMNS)
            result.attrs["invalid_boundary_rows"] = invalid_boundary_rows
            return result
        selected_opened = opened.loc[selected.index]
        selected_closed = closed.loc[selected.index]
        result = pd.DataFrame(
            {
                "provider": "binance",
                "feed": "spot",
                "symbol": symbol,
                "interval": interval.value,
                "open_timestamp": selected_opened,
                "close_timestamp": selected_closed,
                # This is an explicit hypothetical replay clock, never vintage evidence.
                "available_at": selected_closed,
                "revision": 1,
                "finalized": True,
                "open": pd.to_numeric(selected["open"], errors="raise"),
                "high": pd.to_numeric(selected["high"], errors="raise"),
                "low": pd.to_numeric(selected["low"], errors="raise"),
                "close": pd.to_numeric(selected["close"], errors="raise"),
                "volume": pd.to_numeric(selected["volume"], errors="raise"),
                "quote_volume": pd.to_numeric(selected["quote_volume"], errors="raise"),
                "trade_count": pd.to_numeric(selected["trade_count"], errors="raise").astype(int),
                "taker_buy_base_volume": pd.to_numeric(selected["taker_buy_base_volume"], errors="raise"),
                "taker_buy_quote_volume": pd.to_numeric(selected["taker_buy_quote_volume"], errors="raise"),
                "archive_name": name,
                "archive_sha256": checksum,
            }
        )
        result = result.reset_index(drop=True)
        result.attrs["invalid_boundary_rows"] = invalid_boundary_rows
        return result

    def _load_one(
        self,
        frequency: str,
        name: str,
        *,
        symbol: str,
        interval: BarInterval,
        start: datetime,
        end: datetime,
    ) -> tuple[pd.DataFrame, dict[str, Any]] | None:
        payload = self._payload(frequency, symbol, interval, name)
        if payload is None:
            return None
        checksum = hashlib.sha256(payload).hexdigest()
        frame = self._parse(
            payload,
            name=name,
            checksum=checksum,
            symbol=symbol,
            interval=interval,
            start=start,
            end=end,
        )
        return frame, {
            "name": name,
            "frequency": frequency,
            "sha256": checksum,
            "compressed_bytes": len(payload),
            "selected_rows": len(frame),
            "invalid_boundary_rows": int(frame.attrs.get("invalid_boundary_rows", 0)),
            "checksum_verified": True,
        }

    def fetch(
        self,
        *,
        symbol: str,
        interval: BarInterval,
        start: datetime,
        end: datetime,
    ) -> BinanceArchiveResult:
        start, end = require_utc(start), require_utc(end)
        symbol = symbol.strip().upper()
        if _SYMBOL.fullmatch(symbol) is None:
            raise ValueError("Binance archive symbol must contain only 2-30 uppercase letters or digits")
        if end <= start:
            raise ValueError("archive request requires an ordered UTC range")
        frames: list[pd.DataFrame] = []
        manifest: list[dict[str, Any]] = []
        unavailable: list[str] = []
        cursor = _month_start(start)
        while cursor < end:
            month_end = _next_month(cursor)
            selected_start, selected_end = max(start, cursor), min(end, month_end)
            complete_month = selected_start == cursor and selected_end == month_end
            loaded = None
            if complete_month:
                monthly_name = f"{symbol}-{interval.value}-{cursor:%Y-%m}.zip"
                loaded = self._load_one(
                    "monthly",
                    monthly_name,
                    symbol=symbol,
                    interval=interval,
                    start=selected_start,
                    end=selected_end,
                )
            if loaded is not None:
                frame, item = loaded
                frames.append(frame)
                manifest.append(item)
            else:
                for day in _days(selected_start, selected_end):
                    daily_name = f"{symbol}-{interval.value}-{day:%Y-%m-%d}.zip"
                    daily = self._load_one(
                        "daily",
                        daily_name,
                        symbol=symbol,
                        interval=interval,
                        start=selected_start,
                        end=selected_end,
                    )
                    if daily is None:
                        unavailable.append(daily_name)
                        continue
                    frame, item = daily
                    frames.append(frame)
                    manifest.append(item)
            cursor = month_end
        bars = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=_OUTPUT_COLUMNS)
        if not bars.empty:
            bars = bars.sort_values("open_timestamp", kind="stable").reset_index(drop=True)
            duplicates = bars.duplicated("open_timestamp", keep=False)
            if duplicates.any():
                columns = ["open", "high", "low", "close", "volume"]
                for _, group in bars.loc[duplicates].groupby("open_timestamp"):
                    if len(group[columns].drop_duplicates()) != 1:
                        raise ValueError("conflicting Binance archive bars share one timestamp")
                bars = bars.drop_duplicates("open_timestamp", keep="last").reset_index(drop=True)
        return BinanceArchiveResult(bars, tuple(manifest), tuple(unavailable))


__all__ = ["BinanceArchiveResult", "BinancePublicArchive"]
