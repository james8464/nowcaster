#!/usr/bin/env python3
"""Freeze one exploratory intraday selection round; never activates paper trades."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.intraday.selection import SelectionManifest  # noqa: E402
from src.intraday.selection_round import run_registered_selection  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze an exploratory, paper-only intraday selection round")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--input", action="append", required=True, metavar="BROKER_SYMBOL=JSONL")
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        manifest = SelectionManifest.model_validate_json(args.manifest.read_bytes())
        sources = {}
        for item in args.input:
            symbol, separator, filename = item.partition("=")
            if not separator or not symbol or not filename or symbol in sources:
                raise ValueError("each input needs a unique BROKER_SYMBOL=JSONL pair")
            sources[symbol] = Path(filename)
        report = run_registered_selection(manifest, sources, args.output_directory)
    except (OSError, ValueError) as exc:
        # Provider URLs and local source files may carry private account data.
        print(f"Selection round not created: {type(exc).__name__}", file=sys.stderr)
        return 2
    print(f"Retained {len(report.attempts)} attempts; selected {len(report.selected)} exploratory rules.")
    print("No running paper rule was changed; historical results cannot prove live profitability.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
