#!/usr/bin/env python3
"""Paper-only OANDA practice discovery and historical capture.

Never accepts credentials as command-line arguments or exposes order endpoints.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.intraday.contracts import InstrumentSpec  # noqa: E402
from src.intraday.desk import DeskStatus, MarketStatus, OpportunityStatus, PositionStatus  # noqa: E402
from src.intraday.oanda_practice import OandaPracticeFeed  # noqa: E402
from src.intraday.paper import PaperExecutionCosts  # noqa: E402
from src.intraday.runtime import LivePaperRuntime  # noqa: E402


def _now() -> datetime:
    return datetime.now(UTC)


def _publish(directory: Path, status: DeskStatus) -> None:
    target = directory / "summary.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(status.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def _runtime_status(runtime: LivePaperRuntime, at: datetime, *, health: str, opened: bool = False) -> DeskStatus:
    instrument = runtime.instrument
    eligible = "diagnostic" if health == "healthy" else "unverified"
    opportunity = ()
    if opened and health == "healthy":
        position = runtime.account.positions.get(instrument.broker_symbol)
        if position:
            opportunity = (
                OpportunityStatus(
                    market=instrument.market,
                    broker_symbol=instrument.broker_symbol,
                    strategy_id=position.strategy_id,
                    direction=position.direction,
                    decided_at=position.decided_at,
                    entry_at=position.opened_at,
                    entry=position.entry,
                    stop=position.stop,
                    target=position.target,
                    exit_by=position.exit_by,
                    estimated_roundtrip_cost=runtime.costs.slippage_points * 2,
                    explanation="Experimental paper position; historical and prospective profitability unproven.",
                    evidence_hash=position.evidence_hash,
                ),
            )
    positions = tuple(
        PositionStatus(
            market=instrument.market,
            broker_symbol=instrument.broker_symbol,
            direction=position.direction,
            entry=position.entry,
            stop=position.stop,
            target=position.target,
            opened_at=position.opened_at,
            exit_by=position.exit_by,
        )
        for position in runtime.account.positions.values()
    )
    return DeskStatus(
        generated_at=at,
        feed_health=health,
        evidence_status="not_supported",
        markets=(
            MarketStatus(
                market=instrument.market,
                broker_symbol=instrument.broker_symbol,
                product=instrument.product,
                eligibility=eligible,
                reason="Diagnostic paper only; feasibility and forward evidence incomplete.",
            ),
        ),
        opportunities=opportunity,
        paper_positions=positions,
        no_trade_reason="" if opportunity else "No fresh, verified paper entry is available. Stand aside.",
    )


def _candidate(value: str) -> InstrumentSpec:
    try:
        market, symbol, currency, point_value = value.split(":", 3)
        product = "margin_fx" if market == "eurusd" else "cfd"
        return InstrumentSpec(
            provider="oanda_practice",
            market=market,
            broker_symbol=symbol,
            product=product,
            quote_currency=currency,
            point_value=Decimal(point_value),
        )
    except (ValueError, TypeError) as exc:
        raise argparse.ArgumentTypeError("candidate must be market:symbol:currency:point_value") from exc


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise argparse.ArgumentTypeError("time must be explicit UTC")
    return parsed


def _feed() -> OandaPracticeFeed:
    account = os.environ.get("OANDA_PRACTICE_ACCOUNT_ID", "")
    token = os.environ.get("OANDA_PRACTICE_TOKEN", "")
    if not account or not token:
        raise ValueError("OANDA practice account and token must be in environment, not arguments")
    return OandaPracticeFeed(account, token)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Nowcaster paper-only intraday research")
    parser.add_argument(
        "--directory", type=Path, default=Path.home() / "Library/Application Support/Nowcaster/IntradayResearch"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("inventory")
    discover = commands.add_parser("discover")
    discover.add_argument("--candidate", type=_candidate, action="append", required=True)
    fetch = commands.add_parser("fetch")
    fetch.add_argument("--candidate", type=_candidate, required=True)
    fetch.add_argument("--start", type=_time, required=True)
    fetch.add_argument("--end", type=_time, required=True)
    fetch.add_argument("--output", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--candidate", type=_candidate, required=True)
    run.add_argument(
        "--strategy",
        choices=("opening_range_15", "opening_range_30", "trend_pullback", "range_reversion"),
        required=True,
    )
    run.add_argument("--session-open", type=_time, required=True)
    run.add_argument("--session-close", type=_time, required=True)
    run.add_argument("--account-currency", required=True)
    run.add_argument("--costs-file", type=Path, required=True)
    run.add_argument("--protocol-hash", required=True)
    run.add_argument("--initial-cash", type=Decimal, default=Decimal("10000"))
    args = parser.parse_args(argv)
    directory = args.directory.expanduser().resolve()
    if "ProspectiveStudies" in directory.parts or "live-paper-study" in directory.parts:
        parser.error("frozen prospective study path is protected")
    directory.mkdir(parents=True, exist_ok=True)
    summary = directory / "summary.json"
    try:
        if args.command == "status":
            status = (
                DeskStatus.model_validate_json(summary.read_bytes())
                if summary.exists()
                else DeskStatus.unconfigured(datetime.now(UTC))
            )
            print(status.model_dump_json(indent=2))
            return 0
        feed = _feed()
        if args.command == "inventory":
            rows = [
                {
                    "name": item.get("name"),
                    "display_name": item.get("displayName"),
                    "product_type": item.get("type"),
                    "margin_rate": item.get("marginRate"),
                }
                for item in feed.available_instruments()
                if item.get("type") in ("CFD", "CURRENCY")
            ]
            print(json.dumps({"paper_only": True, "instruments": rows}, sort_keys=True))
            return 0
        if args.command == "discover":
            statuses = []
            for candidate in args.candidate:
                try:
                    feed.verify_instrument(candidate)
                    statuses.append(
                        MarketStatus(
                            market=candidate.market,
                            broker_symbol=candidate.broker_symbol,
                            product=candidate.product,
                            eligibility="unverified",
                            reason="Inventory confirmed; history, account quotes, costs and terms remain unverified.",
                        )
                    )
                except ValueError:
                    statuses.append(
                        MarketStatus(
                            market=candidate.market,
                            broker_symbol=candidate.broker_symbol,
                            product=candidate.product,
                            eligibility="rejected",
                            reason="Exact product not available in demo account.",
                        )
                    )
            status = DeskStatus(
                generated_at=datetime.now(UTC),
                feed_health="inventory_verified",
                evidence_status="not_supported",
                markets=tuple(statuses),
                opportunities=(),
                paper_positions=(),
                no_trade_reason="Demo inventory alone cannot qualify paper opportunities. Stand aside.",
            )
            _publish(directory, status)
            print(status.model_dump_json(indent=2))
            return 0
        if args.command == "run":
            if args.session_close <= args.session_open or _now() >= args.session_close:
                raise ValueError("paper session must be current and chronological")
            row = feed.verify_instrument(args.candidate)
            cost_record = json.loads(args.costs_file.read_text(encoding="utf-8"))
            if not cost_record.get("source") or cost_record.get("verified") is not True:
                raise ValueError("cost assumptions need explicit user-attested provenance")
            costs = PaperExecutionCosts.model_validate(
                {
                    key: cost_record[key]
                    for key in ("verified", "slippage_points", "commission_per_unit", "financing_per_unit")
                }
            )
            precision = int(row.get("tradeUnitsPrecision", 0))
            if not 0 <= precision <= 8:
                raise ValueError("unsupported broker unit precision")
            runtime = LivePaperRuntime(
                directory=directory / "studies" / args.protocol_hash,
                protocol_hash=args.protocol_hash,
                instrument=args.candidate,
                strategy_id=args.strategy,
                account_currency=args.account_currency,
                costs=costs,
                session_open=args.session_open,
                session_close=args.session_close,
                initial_cash=args.initial_cash,
                unit_step=Decimal(10) ** -precision,
            )
            try:
                for line in feed.price_lines((args.candidate,)):
                    received = _now()
                    if received > args.session_close + timedelta(seconds=30):
                        break
                    if received < args.session_open:
                        continue
                    quote = feed.parse_price_event(args.candidate, line, received_at=received)
                    if quote is None:
                        if received >= args.session_close and not runtime.account.positions:
                            break
                        continue
                    event = runtime.on_quote(quote)
                    health = (
                        "healthy"
                        if quote.status == "tradeable"
                        and received - quote.observed_at <= timedelta(seconds=5)
                        and (event is None or event.kind != "feed_gap")
                        and received < args.session_close
                        else "stale"
                    )
                    _publish(
                        directory,
                        _runtime_status(
                            runtime, received, health=health, opened=event is not None and event.kind == "opened"
                        ),
                    )
                    if received >= args.session_close and not runtime.account.positions:
                        break
            except httpx.HTTPError:
                received = _now()
                runtime._append("provider_error", received, {"reason": "account_price_stream_failed"})
                _publish(directory, _runtime_status(runtime, received, health="error"))
                raise
            if runtime.account.positions:
                received = _now()
                runtime._append("provider_error", received, {"reason": "paper_position_unresolved_at_session_end"})
                _publish(directory, _runtime_status(runtime, received, health="error"))
                return 2
            if _now() < args.session_close:
                received = _now()
                runtime._append("provider_error", received, {"reason": "account_price_stream_ended_early"})
                _publish(directory, _runtime_status(runtime, received, health="error"))
                return 2
            _publish(directory, _runtime_status(runtime, _now(), health="stale"))
            return 0
        feed.verify_instrument(args.candidate)
        candles = feed.fetch_candles(args.candidate, args.start, args.end, received_at=datetime.now(UTC))
        output = args.output.expanduser().resolve()
        if "ProspectiveStudies" in output.parts or "live-paper-study" in output.parts:
            raise ValueError("frozen prospective study path is protected")
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "schema_version": 1,
                        "price_scope": "historical_base",
                        "instrument": args.candidate.model_dump(mode="json"),
                        "requested_start": args.start.isoformat(),
                        "requested_end": args.end.isoformat(),
                    },
                    sort_keys=True,
                )
                + "\n"
            )
            for candle in candles:
                stream.write(candle.model_dump_json() + "\n")
        print(
            json.dumps({"saved_bars": len(candles), "path": str(output), "price_scope": "historical_base_exploratory"})
        )
        return 0
    except (OSError, ValueError, httpx.HTTPError) as exc:
        # Never print provider exception bodies: they may contain account metadata.
        print(f"Intraday research unavailable: {type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
