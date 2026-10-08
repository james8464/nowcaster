#!/usr/bin/env python3
"""Rebuild a paper-only result from a separate, hash-validated journal."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.intraday.journal import PaperJournal  # noqa: E402
from src.intraday.report import build_report  # noqa: E402


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise argparse.ArgumentTypeError("time must be explicit UTC")
    return parsed


def _atomic(path: Path, data: str) -> None:
    temporary = path.with_name("." + path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild an experimental intraday paper report")
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--protocol-hash", required=True)
    parser.add_argument("--round-id", required=True)
    parser.add_argument("--started-at", type=_utc, required=True)
    args = parser.parse_args(argv)
    directory = args.directory.expanduser().resolve()
    journal = PaperJournal(directory, args.protocol_hash)
    report = build_report(journal, round_id=args.round_id, started_at=args.started_at, as_of=datetime.now(UTC))
    _atomic(directory / "report.json", report.model_dump_json(indent=2) + "\n")
    markdown = (
        f"# Paper result — {report.round_id}\n\n"
        f"Status: {report.evidence_status}. Coverage: {report.coverage_status}.\n\n"
        f"Closed trades: {report.closed_trades}; unresolved positions: {report.open_positions}; "
        f"no-trade decisions: {report.no_trade_count}.\n\n"
        f"Gross P&L: £{report.gross_pnl_gbp}; commission: £{report.commission_gbp}; "
        f"financing: £{report.financing_gbp}; net P&L: £{report.net_pnl_gbp}.\n\n"
        f"Win rate: {report.win_rate if report.win_rate is not None else 'undefined'}; "
        f"net expectancy: {report.net_expectancy_gbp if report.net_expectancy_gbp is not None else 'undefined'}; "
        f"95% daily-block lower bound: "
        f"{report.daily_block_lower_95_gbp if report.daily_block_lower_95_gbp is not None else 'insufficient sample'}.\n\n"
        f"{report.warning}\n"
    )
    _atomic(directory / "report.md", markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
