from __future__ import annotations

import argparse
import asyncio
import json
import sys
from multiprocessing import freeze_support
from pathlib import Path

if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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
    if (
        len(sys.argv) > 2
        and sys.argv[1] == "strategy"
        and sys.argv[2]
        in {
            "background-research",
            "register-background-research",
            "prepare-background-research",
        }
    ):
        from src.background_research.runtime import background_main

        return background_main(sys.argv[2:])
    from src.live_monitor.command import MonitorRuntimeError, parse_bootstrap, replay_events, run_live
    from src.live_monitor.control_input import read_bootstrap_line

    arguments = parser().parse_args()
    try:
        bootstrap = parse_bootstrap(read_bootstrap_line(sys.stdin))
        if arguments.replay is not None:
            for event in replay_events(bootstrap, replay=arguments.replay, provider=arguments.replay_provider):
                print(event.model_dump_json(), flush=True)
        else:
            asyncio.run(run_live(bootstrap, control_stream=sys.stdin))
        return 0
    except MonitorRuntimeError:
        print(json.dumps({"event": "monitor_failed"}), file=sys.stderr, flush=True)
        return 1
    except (EOFError, OSError, ValueError):
        print(json.dumps({"event": "configuration_rejected"}), file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    freeze_support()
    raise SystemExit(main())
