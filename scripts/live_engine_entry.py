from __future__ import annotations

import argparse
import asyncio
import json
import sys
from multiprocessing import freeze_support
from pathlib import Path

from src.live_monitor.command import parse_bootstrap, replay_events, run_live


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Nowcaster notification-only live market engine")
    commands = root.add_subparsers(dest="command", required=True)
    monitor = commands.add_parser("monitor", help="live market monitor")
    monitor_commands = monitor.add_subparsers(dest="monitor_command", required=True)
    run = monitor_commands.add_parser("run", help="run or replay the monitor")
    run.add_argument("--replay", type=Path)
    run.add_argument("--replay-provider", choices=("alpaca", "binance"), default="alpaca")
    return root


def main() -> int:
    arguments = parser().parse_args()
    try:
        bootstrap = parse_bootstrap(input())
        if arguments.replay is not None:
            for event in replay_events(bootstrap, replay=arguments.replay, provider=arguments.replay_provider):
                print(event.model_dump_json(), flush=True)
        else:
            asyncio.run(run_live(bootstrap, control_stream=sys.stdin))
        return 0
    except (EOFError, OSError, ValueError):
        print(json.dumps({"event": "configuration_rejected"}), file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
