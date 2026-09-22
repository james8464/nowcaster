"""User-started public spot research service; no credentials or execution."""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.research.live_paper_notification_bridge import (  # noqa: E402
    read_notification_evidence,
    record_notification_outcome,
    reserve_notification,
)
from src.research.live_paper_signal_runtime import (  # noqa: E402
    LivePaperSignalRunner,
    import_calendar_snapshot,
    read_live_signal_status,
    request_stop,
)


def main(arguments=None):
    parser = argparse.ArgumentParser(description="Live paper-only signal service")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in (
        "start",
        "run-once",
        "status",
        "stop",
        "notification",
        "notification-outcome",
        "notification-evidence",
        "import-calendar",
    ):
        command = commands.add_parser(name)
        command.add_argument("--directory", required=True, type=Path)
        if name == "start":
            command.add_argument("--poll-seconds", type=float, default=5)
        if name == "import-calendar":
            command.add_argument("--file", required=True, type=Path)
        if name in {"notification", "notification-outcome", "notification-evidence"}:
            command.add_argument("--protocol-hash", required=True)
        if name == "notification":
            command.add_argument("--enabled", action="store_true")
        if name in {"notification-outcome", "notification-evidence"}:
            command.add_argument("--material-key", required=True)
        if name == "notification-outcome":
            command.add_argument("--outcome", choices=("delivered", "failed"), required=True)
    args = parser.parse_args(arguments)
    if args.command == "import-calendar":
        print(import_calendar_snapshot(args.directory, args.file).model_dump_json())
        return 0
    if args.command == "notification-evidence":
        import json

        print(
            json.dumps(
                read_notification_evidence(
                    args.directory, protocol_hash=args.protocol_hash, material_key=args.material_key
                )
            )
        )
        return 0
    if args.command == "notification":
        notice = reserve_notification(args.directory, protocol_hash=args.protocol_hash, enabled=args.enabled)
        print(notice.model_dump_json() if notice else "null")
        return 0
    if args.command == "notification-outcome":
        outcome = record_notification_outcome(
            args.directory, protocol_hash=args.protocol_hash, material_key=args.material_key, outcome=args.outcome
        )
        print('{"outcome":"' + outcome + '"}')
        return 0
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
