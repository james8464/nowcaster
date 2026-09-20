"""Run isolated, paper-only Research Round 2 commands without credentials."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.round_two_runtime import (  # noqa: E402
    evaluate_registered_round,
    ingest_file,
    parse_utc,
    register_default_round,
    write_round_report,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Research Round 2 paper-only runner")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("register", "ingest", "evaluate", "status"):
        command = commands.add_parser(name)
        command.add_argument("--directory", type=Path, required=True)
        if name == "register":
            command.add_argument("--starts-at", required=True)
            command.add_argument("--round-id")
        if name == "ingest":
            command.add_argument("--input", type=Path, required=True)
    return parser


def main(arguments: list[str] | None = None) -> int:
    """Execute one local research command; no command accepts credentials or actions."""
    args = _parser().parse_args(arguments)
    if args.command == "register":
        register_default_round(args.directory, parse_utc(args.starts_at), round_id=args.round_id)
    elif args.command == "ingest":
        ingest_file(args.directory, args.input)
    elif args.command == "evaluate":
        evaluate_registered_round(args.directory)
    report_path = write_round_report(args.directory)
    print(json.dumps({"report_path": str(report_path), "paper_only": True, "qualification_status": "unqualified"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
