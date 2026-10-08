#!/usr/bin/env python3
"""Signed-app helper for OANDA practice quotes; no broker order imports/routes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.intraday.contracts import InstrumentSpec  # noqa: E402
from src.intraday.capture_quality import summarize_capture  # noqa: E402
from src.intraday.desk import DeskStatus, MarketStatus  # noqa: E402
from src.intraday.journal import PaperJournal  # noqa: E402
from src.intraday.live_service import LiveIndicatorSession, LiveRoundManifest, LiveRule, LiveSessionWindow  # noqa: E402
from src.intraday.oanda_practice import PRACTICE_API, PRACTICE_STREAM, OandaPracticeFeed  # noqa: E402
from src.intraday.report import aggregate_reports, build_report  # noqa: E402
from src.intraday.session_journal import SessionJournal  # noqa: E402

CATALOG = (
    ("DE30_EUR", "germany40", "cfd", "EUR"),
    ("SPX500_USD", "us500", "cfd", "USD"),
    ("EUR_USD", "eurusd", "margin_fx", "USD"),
    ("WTICO_USD", "wti", "cfd", "USD"),
)


class RetainedAccountMismatch(ValueError):
    """A different practice account requires a separate research directory."""


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _session(now: datetime, instruments: tuple[InstrumentSpec, ...], account_id: str) -> LiveRoundManifest:
    day = now.date()
    windows = {}
    for item in instruments:
        start_hour = 13 if item.market == "wti" else 7
        windows[item.broker_symbol] = LiveSessionWindow(
            opened_at=datetime(day.year, day.month, day.day, start_hour, tzinfo=UTC),
            closed_at=datetime(day.year, day.month, day.day, 20, tzinfo=UTC),
        )
    return LiveRoundManifest(
        round_id=f"diagnostic-{day.isoformat()}",
        account_feed_hash=hashlib.sha256(account_id.encode()).hexdigest(),
        instruments=instruments,
        # Zero selection hash explicitly denotes diagnostic observation, not
        # an elected positive historical rule or paper-entry authorization.
        rules={
            item.broker_symbol: LiveRule(strategy_id="trend_pullback", direction="long", selection_hash="0" * 64)
            for item in instruments
        },
        sessions=windows,
    )


def _validate_retained_account(directory: Path, account_feed_hash: str) -> None:
    for manifest_path in directory.glob("????-??-??/live_round.json"):
        manifest = LiveRoundManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        if manifest.account_feed_hash != account_feed_hash:
            raise RetainedAccountMismatch("practice account changed across retained rounds")


class RetainedReportCache:
    """Validate closed days once, then watch them for changes without rescanning quotes."""

    def __init__(self, directory: Path, account_feed_hash: str):
        self.directory = directory
        self.account_feed_hash = account_feed_hash
        self.closed: dict[str, tuple[object, tuple[tuple[int, int] | None, ...]]] = {}

    def _paths(self, day: str) -> tuple[Path, ...]:
        return (
            self.directory / day / "live_round.json",
            self.directory / day / "quotes.jsonl",
            self.directory / "PaperRounds" / day / "manifest.json",
            self.directory / "PaperRounds" / day / "events.jsonl",
        )

    def _fingerprint(self, day: str) -> tuple[tuple[int, int] | None, ...]:
        fingerprint = []
        for path in self._paths(day):
            stat = path.stat() if path.exists() else None
            fingerprint.append((stat.st_size, stat.st_mtime_ns) if stat else None)
        return tuple(fingerprint)

    def _read_day(self, day: str, as_of: datetime, *, ignore_after_as_of: bool):
        live_directory = self.directory / day
        manifest_path = live_directory / "live_round.json"
        paper_directory = self.directory / "PaperRounds" / day
        if not manifest_path.exists():
            if (paper_directory / "events.jsonl").exists():
                raise ValueError("paper events have no live round identity")
            return None
        manifest = LiveRoundManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        if manifest.account_feed_hash != self.account_feed_hash:
            raise RetainedAccountMismatch("practice account changed across retained rounds")
        paper = PaperJournal(paper_directory, manifest.identity_hash)
        session = SessionJournal(live_directory, manifest.model_dump(mode="json"))
        return build_report(
            paper, manifest, as_of=as_of, session_journal=session, ignore_after_as_of=ignore_after_as_of
        )

    def snapshot(self, as_of: datetime, *, ignore_after_as_of: bool = False) -> dict:
        reports = []
        for paper_directory in sorted((self.directory / "PaperRounds").iterdir()):
            if not paper_directory.is_dir():
                continue
            day = paper_directory.name
            if day in self.closed:
                report, fingerprint = self.closed[day]
                if fingerprint != self._fingerprint(day):
                    raise ValueError("retained closed round changed")
            else:
                report = self._read_day(day, as_of, ignore_after_as_of=ignore_after_as_of)
                if report is not None and day < as_of.date().isoformat():
                    self.closed[day] = (report, self._fingerprint(day))
            if report is not None:
                reports.append(report)
        return aggregate_reports(reports, as_of=as_of).model_dump(mode="json")


def run_paper_indicator(
    directory: Path,
    *,
    account_id: str,
    token: str,
    feed: OandaPracticeFeed | None = None,
    now=None,
) -> None:
    """Observe a practice stream. No paper entry is allowed without a selected rule and verified terms."""
    account_id = account_id.strip()
    token = token.strip()
    if not account_id or not token:
        raise ValueError("practice credentials unavailable")
    if PRACTICE_API != "https://api-fxpractice.oanda.com" or PRACTICE_STREAM != "https://stream-fxpractice.oanda.com":
        raise ValueError("practice host allowlist changed")
    directory = Path(directory).expanduser().resolve()
    if {"ProspectiveStudies", "live-paper-study"} & set(directory.parts):
        raise ValueError("protected study path")
    directory.mkdir(parents=True, exist_ok=True)
    _validate_retained_account(directory, hashlib.sha256(account_id.encode()).hexdigest())
    feed = feed or OandaPracticeFeed(account_id, token)
    now = now or (lambda: datetime.now(UTC))
    inventory = {row.get("name"): row for row in feed.available_instruments()}
    specs = []
    sanitized = []
    for symbol, market, product, currency in CATALOG:
        row = inventory.get(symbol)
        expected = "CURRENCY" if product == "margin_fx" else "CFD"
        if row is None or row.get("type") != expected:
            continue
        specs.append(
            InstrumentSpec(
                provider="oanda_practice",
                broker_symbol=symbol,
                market=market,
                product=product,
                quote_currency=currency,
                point_value=Decimal(1),
            )
        )
        sanitized.append(
            {
                "name": symbol,
                "displayName": row.get("displayName", symbol),
                "type": expected,
                "marginRate": row.get("marginRate"),
            }
        )
    _atomic_json(
        directory / "inventory.json",
        {"paper_only": True, "products": sanitized, "eligibility": "inventory_only_not_paper_eligible"},
    )
    if not specs:
        raise ValueError("no matching practice products")
    instruments = tuple(specs)
    display_names = {row["name"]: str(row["displayName"] or row["name"]) for row in sanitized}
    waiting = DeskStatus(
        generated_at=now(),
        feed_health="inventory_verified",
        evidence_status="not_supported",
        markets=tuple(
            MarketStatus(
                market=item.market,
                broker_symbol=item.broker_symbol,
                display_name=display_names[item.broker_symbol],
                product=item.product,
                eligibility="diagnostic",
                reason="Exact practice product found; cost and selected rule not verified.",
            )
            for item in instruments
        ),
        opportunities=(),
        paper_positions=(),
        no_trade_reason="Awaiting fresh account bid/ask quotes. No paper entry is authorized.",
    )
    _atomic_json(directory / "summary.json", waiting.model_dump(mode="json"))
    initial = now()
    initial_manifest = _session(initial, instruments, account_id)
    active_day = initial.date()
    manifest = initial_manifest
    session = LiveIndicatorSession.restore(directory / active_day.isoformat(), manifest, restart_at=initial)
    reports = RetainedReportCache(directory, manifest.account_feed_hash)
    PaperJournal(directory / "PaperRounds" / active_day.isoformat(), manifest.identity_hash)
    _atomic_json(directory / "report.json", reports.snapshot(initial))

    def publish_quality(as_of: datetime, round_manifest: LiveRoundManifest, round_session: LiveIndicatorSession) -> None:
        events = round_session.journal.events()
        markets = []
        for instrument in round_manifest.instruments:
            window = round_manifest.sessions[instrument.broker_symbol]
            end = max(window.opened_at, min(as_of, window.closed_at))
            markets.append(
                summarize_capture(instrument, window.opened_at, end, events).model_dump(mode="json")
            )
        result = {
            "generated_at": as_of.isoformat(),
            "round_id": round_manifest.round_id,
            "price_scope": "account_stream_observation",
            "paper_eligible": False,
            "markets": markets,
        }
        _atomic_json(round_session.directory / "capture_quality.json", result)
        _atomic_json(directory / "capture_quality.json", result)

    publish_quality(initial, manifest, session)

    def publish_report(
        as_of: datetime,
        round_manifest: LiveRoundManifest,
        round_session: LiveIndicatorSession,
        *,
        concurrent: bool = False,
    ) -> None:
        _atomic_json(directory / "report.json", reports.snapshot(as_of, ignore_after_as_of=concurrent))
        publish_quality(as_of, round_manifest, round_session)

    last_report_at = initial
    last_received_at = initial
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="nowcaster-paper-report") as executor:
        pending: Future[None] | None = None
        try:
            for line in feed.price_lines(instruments):
                received = now()
                last_received_at = received
                if active_day != received.date():
                    if pending is not None:
                        pending.result()
                        pending = None
                    publish_quality(received, manifest, session)
                    active_day = received.date()
                    manifest = _session(received, instruments, account_id)
                    day_directory = directory / active_day.isoformat()
                    session = LiveIndicatorSession.restore(day_directory, manifest, restart_at=received)
                    PaperJournal(directory / "PaperRounds" / active_day.isoformat(), manifest.identity_hash)
                    last_report_at = received - timedelta(seconds=60)
                status = session.on_event(line, received)
                plan = session.new_plan
                status = DeskStatus.model_validate(
                    {
                        **status.model_dump(),
                        "markets": [
                            {**market.model_dump(), "display_name": display_names[market.broker_symbol]}
                            for market in status.markets
                        ],
                    }
                )
                _atomic_json(directory / "summary.json", status.model_dump(mode="json"))
                if (plan is not None or received - last_report_at >= timedelta(seconds=60)) and (
                    pending is None or pending.done()
                ):
                    if pending is not None:
                        pending.result()
                    pending = executor.submit(publish_report, received, manifest, session, concurrent=True)
                    last_report_at = received
                if (directory / "pause.request").exists():
                    return
        finally:
            if pending is not None:
                pending.result()
            publish_report(last_received_at, manifest, session)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nowcaster OANDA practice-only indicator")
    parser.add_argument("run", choices=("run",))
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args(argv)
    account = os.environ.get("OANDA_PRACTICE_ACCOUNT_ID", "")
    token = os.environ.get("OANDA_PRACTICE_TOKEN", "")
    if not account or not token:
        print("Practice credentials are missing from the app session.", file=sys.stderr)
        return 2
    while not (args.directory / "pause.request").exists():
        try:
            run_paper_indicator(args.directory, account_id=account, token=token)
        except RetainedAccountMismatch:
            print("Practice account differs from retained paper research; monitoring stopped.", file=sys.stderr)
            return 2
        except (OSError, ValueError, RuntimeError):
            # Never print an exception containing a URL, account ID or token.
            print("Practice data interrupted; waiting to reconnect.", file=sys.stderr)
        time.sleep(5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
