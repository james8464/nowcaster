"""Immutable historical-only round for the registered SPX500 hypothesis."""

from __future__ import annotations

import hashlib
import json
import os
import random
from collections import defaultdict
from collections.abc import Sequence
from datetime import date, time, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import mean

from src.intraday.contracts import ConfirmedBar
from src.intraday.last_half_hour import NY, _full_session, evaluate_last_half_hour

WINDOWS = {
    "development": (date(2022, 1, 1), date(2024, 1, 1)),
    "validation": (date(2024, 1, 1), date(2025, 1, 1)),
    "sealed": (date(2025, 1, 1), date(2026, 1, 1)),
}
PROTOCOL = {
    "rule": "spx500_first_and_penultimate_half_hours_agree_v1",
    "product": "SPX500_USD",
    "price_scope": "historical_base_exploratory",
    "baseline_slippage_points_per_side": "0.5",
    "stress_slippage_points_per_side": "1.0",
    "conversion_fee_fraction": "0.01",
    "stop_fraction": "0.0025",
    "target_fraction": "0.005",
    "entry_new_york": "15:35",
    "exit_new_york": "16:00",
    "windows": {key: [a.isoformat(), b.isoformat()] for key, (a, b) in WINDOWS.items()},
    "account_fill_claim": False,
}


def _read_captures(paths: Sequence[Path]) -> tuple[list[ConfirmedBar], str]:
    digest = hashlib.sha256()
    bars: list[ConfirmedBar] = []
    for path in paths:
        content = Path(path).read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        lines = content.splitlines()
        if not lines:
            raise ValueError("empty capture")
        header = json.loads(lines[0])
        if (
            header.get("price_scope") != "historical_base"
            or header.get("instrument", {}).get("broker_symbol") != "SPX500_USD"
        ):
            raise ValueError("capture must contain SPX500_USD historical bid/ask bars")
        for line in lines[1:]:
            bar = ConfirmedBar.model_validate_json(line)
            if bar.instrument.broker_symbol != "SPX500_USD" or bar.price_scope != "historical_base":
                raise ValueError("capture must contain SPX500_USD historical bid/ask bars")
            bars.append(bar)
    if not bars or len({bar.start for bar in bars}) != len(bars):
        raise ValueError("capture has no bars or duplicate timestamps")
    return sorted(bars, key=lambda bar: bar.start), digest.hexdigest()


def _daily_lower_bound(values: list[Decimal]) -> str | None:
    if len(values) < 20:
        return None
    rng = random.Random(20261008)
    floats = [float(value) for value in values]
    sample_means = sorted(mean(rng.choices(floats, k=len(floats))) for _ in range(2000))
    return str(Decimal(str(sample_means[49])))


def _always_direction(current: Sequence[ConfirmedBar], direction: str) -> Decimal:
    slip = Decimal("0.5")
    entry_bar, exit_bar = current[73], current[77]
    gross = (
        exit_bar.bid_close - slip - entry_bar.ask_open - slip
        if direction == "long"
        else entry_bar.bid_open - slip - exit_bar.ask_close - slip
    )
    return gross - abs(gross) * Decimal("0.01")


def _maximum_drawdown(values: Sequence[Decimal]) -> Decimal:
    equity = peak = worst = Decimal(0)
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def run_stage(stage: str, captures: Sequence[Path], directory: Path) -> dict:
    """Run once per fixed stage. Never overwrite or promote an exploratory result."""
    if stage not in WINDOWS or not captures:
        raise ValueError("registered stage and captures required")
    directory = Path(directory).expanduser().resolve()
    if {"ProspectiveStudies", "live-paper-study"} & set(directory.parts):
        raise ValueError("protected prospective study path")
    bars, source_hash = _read_captures(captures)
    start, end = WINDOWS[stage]
    grouped: dict[date, list[ConfirmedBar]] = defaultdict(list)
    # Group by exchange-local session date; UTC offsets change with DST.
    for bar in bars:
        local_start = bar.start.astimezone(NY)
        local_end = bar.end.astimezone(NY)
        if (
            local_start.date() == local_end.date()
            and time(9, 30) <= local_start.time()
            and local_end.time() <= time(16)
        ):
            grouped[local_start.date()].append(bar)
    if any(day < start - timedelta(days=7) or day >= end for day in grouped):
        raise ValueError("capture contains dates outside registered stage window")
    days = sorted(day for day in grouped if start <= day < end)
    if not days:
        raise ValueError("capture has no dates inside registered stage window")
    manifest_bytes = (json.dumps(PROTOCOL, sort_keys=True, indent=2) + "\n").encode()
    if directory.exists():
        manifest = directory / "protocol.json"
        if manifest.exists() and manifest.read_bytes() != manifest_bytes:
            raise ValueError("research protocol changed")
    if stage != "development":
        prerequisite = "development" if stage == "validation" else "validation"
        if not (directory / f"{prerequisite}.json").exists():
            raise ValueError("earlier research stage must precede this one")
    target = directory / f"{stage}.json"
    if target.exists():
        raise FileExistsError("research stage already recorded")
    outcomes = []
    incomplete = 0
    eligible = 0
    all_days = sorted(grouped)
    for day in days:
        prior_candidates = [item for item in all_days if item < day and (day - item).days <= 7]
        prior = grouped[prior_candidates[-1]] if prior_candidates else []
        current = grouped[day]
        if not (_full_session(prior) and _full_session(current)):
            incomplete += 1
            continue
        eligible += 1
        base = evaluate_last_half_hour(prior, current, slippage_points=Decimal("0.5"))
        stress = evaluate_last_half_hour(prior, current, slippage_points=Decimal("1.0"))
        outcomes.append({
            "day": day.isoformat(), "direction": base.direction, "reason": base.reason,
            "baseline_net_points": str(base.net_points), "stressed_net_points": str(stress.net_points),
            "always_long_net_points": str(_always_direction(current, "long")),
            "always_short_net_points": str(_always_direction(current, "short")),
            "price_scope": base.price_scope,
        })
    pnl = [Decimal(row["baseline_net_points"]) for row in outcomes]
    stress_pnl = [Decimal(row["stressed_net_points"]) for row in outcomes]
    result = {
        "stage": stage, "protocol_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "source_sha256": source_hash, "price_scope": "historical_base_exploratory",
        "account_fill_claim": False, "eligible_days": eligible, "incomplete_days": incomplete,
        "trade_count": sum(row["direction"] is not None for row in outcomes),
        "long_count": sum(row["direction"] == "long" for row in outcomes),
        "short_count": sum(row["direction"] == "short" for row in outcomes),
        "baseline_points": "0", "total_net_points": str(sum(pnl, Decimal(0))),
        "stressed_net_points": str(sum(stress_pnl, Decimal(0))),
        "always_long_net_points": str(sum((Decimal(row["always_long_net_points"]) for row in outcomes), Decimal(0))),
        "always_short_net_points": str(sum((Decimal(row["always_short_net_points"]) for row in outcomes), Decimal(0))),
        "maximum_drawdown_points": str(_maximum_drawdown(pnl)),
        "daily_mean_95pct_lower_bound_points": _daily_lower_bound(pnl),
        "days": outcomes,
        "limitations": (
            "Historical base-price candles, not account-specific fills; "
            "GBP conversion and financing unavailable."
        ),
    }
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "protocol.json"
    if not manifest.exists() and any((directory / f"{name}.json").exists() for name in WINDOWS):
        raise ValueError("research stage exists without its protocol")
    if not manifest.exists():
        with manifest.open("xb") as stream:
            stream.write(manifest_bytes)
            stream.flush()
            os.fsync(stream.fileno())
    with target.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    return result
