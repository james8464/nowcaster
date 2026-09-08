#!/usr/bin/env python3
"""Run the bounded hourly holding-period development search."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from src.config.settings import Settings
from src.ingestion.bars import atomic_write_bytes
from src.ingestion.binance_archive import BinancePublicArchive
from src.research.holding_period_search import STRATEGY_IDS, search_scope
from src.research.opportunity_audit import gap_safe_atr
from src.strategies.library import build_strategy_registry
from src.strategies.registry import StrategyRegistry
from src.strategies.types import BarInterval, canonical_hash

SYMBOLS = ("BTCUSDT", "ETHUSDT")


def _research_registry(root: Path) -> StrategyRegistry:
    source = build_strategy_registry(Settings.load(root.resolve(), mode="live").strategies.enabled)
    selected = StrategyRegistry()
    available = {item.spec.strategy_id: item for item in source.enabled()}
    missing = set(STRATEGY_IDS) - set(available)
    if missing:
        raise ValueError(f"configured research registry is missing strategies: {sorted(missing)}")
    for strategy_id in STRATEGY_IDS:
        item = available[strategy_id]
        if BarInterval.ONE_HOUR not in item.spec.intervals:
            raise ValueError(f"research strategy does not support hourly bars: {strategy_id}")
        selected.register(item.spec, item.generator, item.metadata)
    return selected


def _report(discovery: dict[str, Any]) -> str:
    rows = [
        "# Bounded holding-period development search",
        "",
        "This is retrospective development evidence only. It is not promotable to alerts, orders, or real money.",
        "",
        "| Asset | Selected candidate | Screen passed | Worst-fold stressed mean |",
        "|---|---|---:|---:|",
    ]
    for candidate in discovery["candidates"]:
        worst = min(
            (
                float(fold["mean_stressed_return"])
                for fold in candidate["folds"]
                if fold["mean_stressed_return"] is not None
            ),
            default=float("-inf"),
        )
        worst_text = "unavailable" if worst == float("-inf") else f"{worst:.4%}"
        rows.append(
            f"| {candidate['symbol']} | `{candidate['candidate_id']}` | "
            f"{'yes' if candidate['screen_passed'] else 'no'} | {worst_text} |"
        )
    rows.extend(
        [
            "",
            "## Fixed method",
            "",
            "- Exactly 24 predeclared trials: two assets, three causal hourly strategies, two stops, and two expiries.",
            "- Four chronological development folds; trades crossing a fold boundary are not scored in that fold.",
            "- Next-continuous-bar entry, stop-first ambiguous candles, and expiry-before-target.",
            "- 34 bps modeled round-trip cost, 68 bps stressed cost, and a 68 bps minimum target distance.",
            "- At most one candidate per asset is retained for experimental observation, even when its screen fails.",
            "",
            "A failed historical screen remains visible and can never support a positive paper-study verdict.",
            "",
        ]
    )
    return "\n".join(rows)


def run_search(
    root: Path,
    cache_dir: Path,
    output_dir: Path,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Fetch verified hourly archives and persist the fixed 24-trial discovery."""

    if (
        start.tzinfo is None
        or end.tzinfo is None
        or start.utcoffset() != timedelta(0)
        or end.utcoffset() != timedelta(0)
    ):
        raise ValueError("search boundaries must be explicit UTC")
    if end <= start:
        raise ValueError("exclusive end must follow start")
    registry = _research_registry(root)
    scopes: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        archive = BinancePublicArchive(client, cache_dir=cache_dir.expanduser().resolve())
        for symbol in SYMBOLS:
            fetched = archive.fetch(
                symbol=symbol,
                interval=BarInterval.ONE_HOUR,
                start=start.astimezone(UTC),
                end=end.astimezone(UTC),
            )
            if fetched.bars.empty:
                raise ValueError(f"no verified hourly archive bars were available for {symbol}")
            bars = fetched.bars.copy()
            bars["atr"] = gap_safe_atr(bars, period=14)
            scopes.append(search_scope(bars, symbol=symbol, registry=registry))
            manifests.extend({"symbol": symbol, **dict(item)} for item in fetched.manifest)
    trials = [configuration for scope in scopes for configuration in scope["configurations"]]
    candidates = [scope["selected_candidate"] for scope in scopes]
    discovery = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "start": start.astimezone(UTC).isoformat(),
        "end_exclusive": end.astimezone(UTC).isoformat(),
        "symbols": list(SYMBOLS),
        "interval": BarInterval.ONE_HOUR.value,
        "chronology": {"kind": "four_chronological_development_folds", "folds": 4},
        "assumptions": scopes[0]["assumptions"],
        "archive_manifests": manifests,
        "archive_manifest_hash": canonical_hash(manifests),
        "trials": trials,
        "candidates": candidates,
        "promotable": False,
        "status": "experimental_observation_only",
        "note": "Retrospective development search; no independent validation and no live strategy promotion.",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(
        output_dir / "discovery.json",
        (json.dumps(discovery, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(),
    )
    atomic_write_bytes(output_dir / "report.md", _report(discovery).encode())
    return discovery


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise argparse.ArgumentTypeError("timestamp must include an explicit UTC offset")
    return parsed.astimezone(UTC)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--cache-dir", type=Path, default=Path("~/Library/Caches/NowcasterOpportunityAudit"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--start", type=_utc, required=True)
    parser.add_argument("--end-exclusive", type=_utc, required=True)
    args = parser.parse_args()
    result = run_search(args.root, args.cache_dir, args.output_dir, args.start, args.end_exclusive)
    print(json.dumps({"trials": len(result["trials"]), "candidates": len(result["candidates"])}))


if __name__ == "__main__":
    main()
