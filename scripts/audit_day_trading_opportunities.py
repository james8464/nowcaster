"""Run a checksum-verified, non-promotable intraday opportunity audit."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from src.config.settings import Settings
from src.ingestion.bars import INTERVAL_DURATION, atomic_write_bytes
from src.ingestion.binance_archive import BinancePublicArchive
from src.live_monitor.levels import DEFAULT_TRADE_LEVEL_POLICY
from src.models.trade_outcomes import BarrierPolicy
from src.research.opportunity_audit import OpportunityResearchProtocol, audit_opportunity_scope, gap_safe_atr
from src.strategies.library import build_strategy_registry
from src.strategies.registry import StrategyRegistry
from src.strategies.types import BarInterval, canonical_hash

_EXCLUDED = {
    "opening_range_breakout",
    "etf_last_half_hour_momentum",
    "rolling_cointegration_pairs",
    "crypto_cross_sectional_momentum",
}
_SUPPORTED_INTERVALS = frozenset({BarInterval.FIVE_MINUTES, BarInterval.FIFTEEN_MINUTES, BarInterval.ONE_HOUR})


def _barrier_policy() -> BarrierPolicy:
    return BarrierPolicy(
        target_r=float(DEFAULT_TRADE_LEVEL_POLICY.minimum_target_2_r),
        stop_r=float(DEFAULT_TRADE_LEVEL_POLICY.atr_multiplier),
        maximum_bars=DEFAULT_TRADE_LEVEL_POLICY.expires_after_bars,
        round_trip_cost_bps=34,
    )


def _instant(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise ValueError("audit boundaries must be explicit UTC")
    return result.astimezone(UTC)


def _selected_registry(settings: Settings, symbol: str, interval: BarInterval) -> StrategyRegistry:
    source = build_strategy_registry(settings.strategies.enabled)
    selected = StrategyRegistry()
    for item in source.enabled():
        if interval not in item.spec.intervals or item.spec.strategy_id in _EXCLUDED:
            continue
        if item.spec.strategy_id == "bitcoin_active_session_momentum" and symbol != "BTCUSDT":
            continue
        selected.register(item.spec, item.generator, item.metadata)
    return selected


def _quality(bars, interval: BarInterval) -> dict[str, Any]:
    if bars.empty:
        return {"bars": 0, "gap_events": 0, "missing_bars": 0, "duplicate_open_times": 0}
    duration = INTERVAL_DURATION[interval]
    opens = bars["open_timestamp"].sort_values().reset_index(drop=True)
    missing = [
        max(0, round((later - earlier) / duration) - 1) for earlier, later in zip(opens, opens.iloc[1:], strict=False)
    ]
    return {
        "bars": len(bars),
        "first_open": opens.iloc[0].isoformat(),
        "last_open": opens.iloc[-1].isoformat(),
        "gap_events": sum(value > 0 for value in missing),
        "missing_bars": sum(missing),
        "duplicate_open_times": int(opens.duplicated().sum()),
    }


def _scope_row(scope: dict[str, Any]) -> str:
    diagnostic = scope.get("diagnostic_all_component_consensus") or {}
    holdout = diagnostic.get("holdout") or {}
    mean = holdout.get("mean_net_return")
    mean_text = "—" if mean is None else f"{float(mean) * 10_000:.2f} bps"
    return (
        f"| {scope['symbol']} | {scope['interval']} | {scope['data_quality']['bars']:,} | "
        f"{scope['data_quality']['missing_bars']:,} | {len(scope.get('strategies', []))} | "
        f"{len(scope['selected_components'])} | "
        f"{int(holdout.get('opportunities', 0)):,} | {mean_text} | {scope['decision']['status']} |"
    )


def _report(summary: dict[str, Any]) -> str:
    diagnostic_holdouts = [
        (scope.get("diagnostic_all_component_consensus") or {}).get("holdout") or {} for scope in summary["scopes"]
    ]
    gross_means = [
        float(item["mean_gross_return"]) * 10_000
        for item in diagnostic_holdouts
        if item.get("mean_gross_return") is not None
    ]
    net_means = [
        float(item["mean_net_return"]) * 10_000
        for item in diagnostic_holdouts
        if item.get("mean_net_return") is not None
    ]
    diagnostic_range = (
        f"Across the diagnostic holdouts, mean return before costs ranged from {min(gross_means):.2f} to "
        f"{max(gross_means):.2f} bps per setup; after modeled costs it ranged from {min(net_means):.2f} to "
        f"{max(net_means):.2f} bps."
        if gross_means and net_means
        else "No diagnostic holdout had enough scorable opportunities to estimate a return."
    )
    lines = [
        "# Day-trading opportunity audit",
        "",
        f"**Window:** {summary['start']} to {summary['end']} (exclusive cutoff)",
        "",
        f"**Archive evidence:** {summary['verified_archive_files']:,} checksum-verified files; "
        f"{summary['invalid_archive_boundary_rows']:,} impossible boundary rows excluded",
        "",
        f"**Archive manifest SHA-256:** `{summary['archive_manifest_hash']}`",
        "",
        f"**Outcome:** `{summary['overall_decision']}`. Real-money status remains **locked**.",
        "",
        "This test asks a practical question: after a candle closed, did a non-overlapping setup entered on the "
        "next continuous candle reach its take-profit before its stop, after conservative round-trip costs? "
        "An ambiguous candle counts as a stop and missing bars invalidate the affected opportunity.",
        "",
        "| Asset | Interval | Bars | Missing bars | Directional rules tested | Rules selected | "
        "Diagnostic holdout setups | Diagnostic mean after costs | Result |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
        *[_scope_row(scope) for scope in summary["scopes"]],
        "",
        "## Interpretation",
        "",
        "A raw indicator firing is not a trading opportunity. A rule first has to show positive conservative "
        "edge in development and validation, survive doubled costs, and then pass a later untouched holdout. "
        "The diagnostic columns combine every tested rule only to measure how the unqualified signal library behaved; "
        "they are not a candidate ensemble and cannot be traded. "
        "No archive result can unlock alerts: archives may be corrected after publication and cannot recreate "
        "the data revision state or fill quality that existed at the original decision time.",
        "",
        diagnostic_range,
        "",
        "Short signals in this report are hypotheses only. The configured Binance Spot instruments are not "
        "shortable; a derivatives or margin product would need its own funding, liquidation, fee, order-book, "
        "and forward-validation evidence.",
        "",
        "## Protocol",
        "",
        "- Official Binance public monthly/daily kline ZIP files; every used file passed its published SHA-256.",
        "- Fixed 60% development, 20% validation, 20% holdout chronology.",
        "- Every configured rule's long and short hypotheses are gated independently.",
        "- Bootstrap evidence uses a Bonferroni family-wise correction across every direction and selection split.",
        "- Next-bar entry, 1 ATR screening stop, 1.5R target, and the live monitor's exact three-bar expiry.",
        "- 34 bps conservative round trip: two 10 bps taker fees, two 2 bps half-spreads, "
        "and two 5 bps slippage charges.",
        "- One open hypothetical setup per rule; right-censored and gap-crossing outcomes are excluded.",
        "- Archive rows with impossible interval boundaries are excluded and counted as missing data.",
        "- Same-candle TP/SL ambiguity is scored stop-first; no unfinished candle or future row enters a decision.",
        "- Component selection uses development and validation only; the final holdout cannot change the selected set.",
        "",
        "## Source and limitations",
        "",
        "- [Binance public data documentation](https://github.com/binance/binance-public-data/blob/master/README.md)",
        "- [Documented archive inconsistency example](https://github.com/binance/binance-public-data/issues/475)",
        "",
        "This is a rejection/diagnostic test, not proof of future profit. Candle data does not reconstruct queue "
        "position, transient spread, market impact, outages, taxes, borrow, funding, liquidations, or human execution. "
        "Any surviving candidate still requires a frozen forward shadow period and then paper trading.",
        "",
    ]
    return "\n".join(lines)


def run_audit(
    *,
    root: Path,
    cache_dir: Path,
    output_dir: Path,
    symbols: tuple[str, ...],
    intervals: tuple[BarInterval, ...],
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    root = root.resolve()
    cache_dir = cache_dir.expanduser().resolve()
    output_dir = output_dir.resolve()
    if cache_dir == root or root in cache_dir.parents:
        raise ValueError("archive cache must remain outside the repository")
    settings = Settings.load(root, mode="live")
    protocol = OpportunityResearchProtocol()
    scopes: list[dict[str, Any]] = []
    archive_manifests: list[dict[str, Any]] = []
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        archive = BinancePublicArchive(client, cache_dir=cache_dir)
        for symbol in symbols:
            for interval in intervals:
                print(json.dumps({"event": "scope_started", "symbol": symbol, "interval": interval.value}), flush=True)
                fetched = archive.fetch(symbol=symbol, interval=interval, start=start, end=end)
                quality = _quality(fetched.bars, interval)
                manifests = [{"symbol": symbol, "interval": interval.value, **item} for item in fetched.manifest]
                archive_manifests.extend(manifests)
                if fetched.bars.empty:
                    scopes.append(
                        {
                            "symbol": symbol,
                            "interval": interval.value,
                            "data_quality": quality,
                            "selected_components": [],
                            "candidate_ensemble": None,
                            "decision": {"status": "unavailable", "reason": "no archive bars were available"},
                            "unavailable_files": list(fetched.unavailable),
                        }
                    )
                    continue
                bars = fetched.bars.copy()
                bars["atr"] = gap_safe_atr(bars, period=14)
                policy = _barrier_policy()
                result = audit_opportunity_scope(
                    bars,
                    _selected_registry(settings, symbol, interval),
                    policy,
                    protocol=protocol,
                )
                result["data_quality"] = quality
                result["archive"] = {
                    "verified_files": len(fetched.manifest),
                    "unavailable_files": len(fetched.unavailable),
                    "invalid_boundary_rows": sum(
                        int(item.get("invalid_boundary_rows", 0)) for item in fetched.manifest
                    ),
                    "manifest_hash": canonical_hash(manifests),
                }
                scopes.append(result)
                print(
                    json.dumps(
                        {
                            "event": "scope_complete",
                            "symbol": symbol,
                            "interval": interval.value,
                            "decision": result["decision"]["status"],
                            "selected_components": len(result["selected_components"]),
                        }
                    ),
                    flush=True,
                )
    candidates = [
        scope
        for scope in scopes
        if scope["decision"]["status"] == "retrospective_candidate_found_forward_test_required"
    ]
    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "evidence_tier": "retrospective_archive_only",
        "eligible_for_live_promotion": False,
        "overall_decision": (
            "retrospective_candidate_found_forward_test_required" if candidates else "no_reliable_strategy_found"
        ),
        "live_money_status": "locked",
        "symbols": list(symbols),
        "intervals": [item.value for item in intervals],
        "verified_archive_files": len(archive_manifests),
        "invalid_archive_boundary_rows": sum(int(item.get("invalid_boundary_rows", 0)) for item in archive_manifests),
        "archive_manifest_hash": canonical_hash(archive_manifests),
        "scopes": scopes,
        "source": "official Binance public spot kline archives with published SHA-256 checksums",
        "note": (
            "Retrospective archives cannot qualify live alerts; forward shadow and paper evidence remain mandatory."
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(
        output_dir / "summary.json",
        (json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(),
    )
    atomic_write_bytes(
        output_dir / "archive-manifest.json",
        (json.dumps(archive_manifests, indent=2, sort_keys=True, allow_nan=False) + "\n").encode(),
    )
    atomic_write_bytes(output_dir / "report.md", _report(summary).encode())
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path.home() / "Library" / "Caches" / "NowcasterOpportunityAudit",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("build/day-trading-opportunity-audit"))
    parser.add_argument("--start", default="2017-08-17T00:00:00Z")
    parser.add_argument("--end", required=True, help="Exclusive fixed UTC cutoff, normally the start of today.")
    parser.add_argument("--symbol", action="append", dest="symbols")
    parser.add_argument("--interval", action="append", dest="intervals")
    arguments = parser.parse_args()
    intervals = tuple(BarInterval(value) for value in (arguments.intervals or ("5m", "15m", "1h")))
    if any(interval not in _SUPPORTED_INTERVALS for interval in intervals):
        raise ValueError("day-trading audit supports 5m, 15m, and 1h intervals")
    summary = run_audit(
        root=arguments.project_root,
        cache_dir=arguments.cache_dir,
        output_dir=arguments.output_dir,
        symbols=tuple(symbol.upper() for symbol in (arguments.symbols or ("BTCUSDT", "ETHUSDT"))),
        intervals=intervals,
        start=_instant(arguments.start),
        end=_instant(arguments.end),
    )
    print(
        json.dumps(
            {
                "event": "audit_complete",
                "decision": summary["overall_decision"],
                "live_money_status": summary["live_money_status"],
            }
        )
    )


if __name__ == "__main__":
    main()
