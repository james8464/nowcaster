#!/usr/bin/env python3
"""Register a future, unqualified OANDA practice paper round; never starts a feed."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.intraday.experimental_round import ExperimentalRoundManifest, register_experimental_round  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest="command", required=True)
    register = command.add_parser("register")
    register.add_argument("--manifest", type=Path, required=True)
    register.add_argument("--directory", type=Path, required=True)
    register.add_argument(
        "--cost-evidence",
        type=Path,
        required=True,
        help="Sanitized exact-product cost record; SHA-256 must match manifest",
    )
    args = parser.parse_args()
    manifest = ExperimentalRoundManifest.model_validate_json(args.manifest.read_bytes())
    print(register_experimental_round(args.directory, manifest, cost_evidence=args.cost_evidence.read_bytes()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
