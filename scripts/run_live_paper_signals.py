"""User-started public spot research service; no credentials or execution."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.live_paper_signal_runtime import (  # noqa: E402
    LivePaperSignalRunner,
    read_live_signal_status,
    request_stop,
)


def main(arguments=None):
    parser = argparse.ArgumentParser(description="Live paper-only signal service")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "run-once", "status", "stop"):
        command = commands.add_parser(name)
        command.add_argument("--directory", required=True, type=Path)
        if name == "start":
            command.add_argument("--poll-seconds", type=float, default=5)
    args = parser.parse_args(arguments)
    if args.command == "start":
        state = LivePaperSignalRunner().start(args.directory, poll_seconds=args.poll_seconds)
    elif args.command == "run-once":
        state = LivePaperSignalRunner().run_once(args.directory)
    else:
        if args.command == "stop":
            request_stop(args.directory)
        state = read_live_signal_status(args.directory)
    print(state.model_dump_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
