#!/usr/bin/env python3
"""Signed-app helper for OANDA practice quotes; no broker order imports/routes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.intraday.contracts import InstrumentSpec  # noqa: E402
from src.intraday.desk import DeskStatus, MarketStatus  # noqa: E402
from src.intraday.journal import PaperJournal  # noqa: E402
from src.intraday.live_service import LiveIndicatorSession, LiveRoundManifest, LiveRule, LiveSessionWindow  # noqa: E402
from src.intraday.oanda_practice import PRACTICE_API, PRACTICE_STREAM, OandaPracticeFeed  # noqa: E402
from src.intraday.report import build_report  # noqa: E402

CATALOG = (
    ("DE30_EUR", "germany40", "cfd", "EUR"),
    ("SPX500_USD", "us500", "cfd", "USD"),
    ("EUR_USD", "eurusd", "margin_fx", "USD"),
    ("WTICO_USD", "wti", "cfd", "USD"),
)


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


def run_paper_indicator(
    directory: Path,
    *,
    account_id: str,
    token: str,
    feed: OandaPracticeFeed | None = None,
    now=None,
) -> None:
    """Observe a practice stream. No paper entry is allowed without a selected rule and verified terms."""
    if not account_id or not token:
        raise ValueError("practice credentials unavailable")
    if PRACTICE_API != "https://api-fxpractice.oanda.com" or PRACTICE_STREAM != "https://stream-fxpractice.oanda.com":
        raise ValueError("practice host allowlist changed")
    directory = Path(directory).expanduser().resolve()
    if {"ProspectiveStudies", "live-paper-study"} & set(directory.parts):
        raise ValueError("protected study path")
    directory.mkdir(parents=True, exist_ok=True)
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
    waiting = DeskStatus(
        generated_at=now(),
        feed_health="inventory_verified",
        evidence_status="not_supported",
        markets=tuple(
            MarketStatus(
                market=item.market,
                broker_symbol=item.broker_symbol,
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
    initial_paper = PaperJournal(directory / "PaperRounds" / initial.date().isoformat(), initial_manifest.identity_hash)
    _atomic_json(
        directory / "report.json", build_report(initial_paper, initial_manifest, as_of=initial).model_dump(mode="json")
    )
    active_day = None
    session = None
    last_report_at = initial
    for line in feed.price_lines(instruments):
        received = now()
        if active_day != received.date():
            active_day = received.date()
            manifest = _session(received, instruments, account_id)
            day_directory = directory / active_day.isoformat()
            session = LiveIndicatorSession.restore(day_directory, manifest)
            paper_journal = PaperJournal(directory / "PaperRounds" / active_day.isoformat(), manifest.identity_hash)
            report = build_report(paper_journal, manifest, as_of=received)
            _atomic_json(directory / "report.json", report.model_dump(mode="json"))
            last_report_at = received
        assert session is not None
        status = session.on_event(line, received)
        _atomic_json(directory / "summary.json", status.model_dump(mode="json"))
        if received - last_report_at >= timedelta(seconds=60):
            report = build_report(paper_journal, manifest, as_of=received, session_journal=session.journal)
            _atomic_json(directory / "report.json", report.model_dump(mode="json"))
            last_report_at = received
        if (directory / "pause.request").exists():
            return


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
        except (OSError, ValueError, RuntimeError):
            # Never print an exception containing a URL, account ID or token.
            print("Practice data interrupted; waiting to reconnect.", file=sys.stderr)
        time.sleep(5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
