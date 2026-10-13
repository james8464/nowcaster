#!/usr/bin/env python3
"""Read-only TradingView/Nowcaster OR15 direction comparison; never compares fills."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.intraday.pine_parity import compare_pine_decisions  # noqa: E402
from src.strategies.types import canonical_hash  # noqa: E402


def _jsonl(path: Path) -> list[dict]:
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        raise ValueError("unterminated JSONL input")
    if any(len(line) > 65536 for line in data.splitlines()):
        raise ValueError("oversized JSONL row")
    rows = [json.loads(line) for line in data.splitlines()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("JSONL rows must be objects")
    return rows


def _verified_account_decisions(directory: Path) -> list[dict]:
    if not (directory / "live_round.json").is_file():
        raise ValueError("account session manifest missing")
    events = _jsonl(directory / "quotes.jsonl")
    previous = "0" * 64
    for event in events:
        saved = event.get("record_hash")
        if not isinstance(saved, str) or event.get("previous_hash") != previous:
            raise ValueError("account journal chain mismatch")
        unsigned = {key: value for key, value in event.items() if key != "record_hash"}
        if canonical_hash(unsigned) != saved:
            raise ValueError("account journal hash mismatch")
        previous = saved
    return [event for event in events if event.get("kind") == "decision"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pine-alerts", type=Path, required=True)
    parser.add_argument("--account-session", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    session = args.account_session.resolve(strict=True)
    output = args.output.resolve()
    if output == session or session in output.parents:
        parser.error("comparison output must be outside the retained account session")
    try:
        result = compare_pine_decisions(_jsonl(args.pine_alerts), _verified_account_decisions(session))
        report = {
            "schema_version": 1,
            "paper_only": True,
            "price_fill_parity_supported": False,
            "account_journal_chain_verified": True,
            "direction_matches": result.direction_matches,
            "discrepancies": result.discrepancies,
            "rows": [asdict(row) for row in result.rows],
        }
        with output.open("x", encoding="utf-8") as stream:
            json.dump(report, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
