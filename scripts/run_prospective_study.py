"""Register, collect or inspect an isolated public-feed paper experiment."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.prospective_runtime import DEFAULT_DIRECTORY, register_study, run_study  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register", help="freeze a new future study; retain all previous campaigns")
    register.add_argument("--discovery", type=Path, required=True)
    register.add_argument(
        "--directory", type=Path, default=DEFAULT_DIRECTORY / datetime.now(UTC).strftime("study-%Y%m%dT%H%M%SZ")
    )
    register.add_argument("--starts-at", help="explicit future UTC timestamp; default is two minutes ahead")
    run = commands.add_parser("run", help="resume observation without resetting the experiment")
    run.add_argument("--directory", type=Path, required=True)
    run.add_argument("--duration-seconds", type=float, default=21600)
    status = commands.add_parser("status", help="read the last published report, even while collector owns ledger")
    status.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        directory = args.directory.expanduser().resolve()
        if args.command == "register":
            starts_at = datetime.fromisoformat(args.starts_at.replace("Z", "+00:00")) if args.starts_at else None
            manifest = register_study(args.discovery.expanduser(), directory, root=ROOT, starts_at=starts_at)
            print(
                json.dumps(
                    {
                        "study_id": manifest.study_id,
                        "directory": str(directory),
                        "starts_at": manifest.starts_at.isoformat(),
                        "ends_at": manifest.ends_at.isoformat(),
                    }
                )
            )
        elif args.command == "run":
            summary = asyncio.run(run_study(directory, root=ROOT, duration_seconds=args.duration_seconds))
            print(json.dumps(summary, indent=2, default=str))
        else:
            summary = json.loads((directory / "summary.json").read_text())
            updated = datetime.fromisoformat(summary["updated_at"].replace("Z", "+00:00"))
            summary["published_report_age_seconds"] = max(0, (datetime.now(UTC) - updated).total_seconds())
            print(json.dumps(summary, indent=2, default=str))
        return 0
    except KeyboardInterrupt:
        print("Collection stopped; retained evidence can be resumed.", file=sys.stderr)
        return 130
    except (ValueError, OSError, RuntimeError) as error:
        print(f"Paper study refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
