#!/usr/bin/env python3
"""Run one immutable, paper-only SPX500 historical research stage."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.intraday.last_half_hour_round import WINDOWS, run_stage  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Exploratory SPX500 historical research; never account fills")
    parser.add_argument("--stage", choices=tuple(WINDOWS), required=True)
    parser.add_argument("--capture", type=Path, action="append", required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = run_stage(args.stage, args.capture, args.directory)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Historical research unavailable: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps({key: value for key, value in result.items() if key != "days"}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
