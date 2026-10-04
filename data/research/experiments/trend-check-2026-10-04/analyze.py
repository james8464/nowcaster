"""Reproducible research analysis, not an app feature or live qualification path.

Run with PYTHONPATH pointing to the pinned checkout and one new output directory.
Uses existing archive, strategy, causality and opportunity-audit implementations.
"""

import hashlib
import json
import math
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
from scipy.stats import t

from src.backtest.opportunities import audit_strategy_opportunities
from src.config.settings import Settings
from src.ingestion.binance_archive import BinancePublicArchive
from src.models.trade_outcomes import BarrierPolicy
from src.research.holding_period_search import research_runtime_fingerprint
from src.research.opportunity_audit import _gap_safe_signals, gap_safe_atr
from src.strategies.library import StrategyContext, audit_prefix_invariance, build_strategy_registry
from src.strategies.types import BarInterval

ROOT = Path.cwd()
HERE = Path(__file__).resolve().parent
PROTOCOL = json.loads((HERE / "protocol.json").read_text())
OUT = Path(sys.argv[1]).expanduser().resolve()
OUT.mkdir(parents=True, exist_ok=False)


def save(name, payload):
    with (OUT / name).open("x") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True, default=str, allow_nan=False)
        handle.write("\n")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def metrics(outcomes, cost):
    if outcomes.empty:
        return {
            "trades": 0,
            "exit_days": 0,
            "mean_net_bps": None,
            "mean_gross_bps": None,
            "closed_trade_index_drawdown": None,
            "daily_lower_net_bps": None,
            "net_win_rate": None,
        }
    gross = outcomes.gross_return.to_numpy(dtype=float)
    net = gross - cost / 10000
    if not np.isfinite(net).all() or (net <= -1).any():
        raise ValueError("invalid trade return")
    dates = pd.to_datetime(outcomes.exit_timestamp, utc=True).dt.date
    daily = pd.Series(net, index=dates).groupby(level=0).mean()
    lower = None
    if len(daily) >= 2:
        # 6 candidates x 2 date windows, fixed before outcomes. Diagnostic only.
        lower = (
            float(daily.mean() - t.ppf(1 - 0.05 / 12, len(daily) - 1) * daily.std(ddof=1) / math.sqrt(len(daily)))
            * 10000
        )
    equity = np.r_[1.0, np.cumprod(1 + net)]
    return {
        "trades": len(net),
        "exit_days": len(daily),
        "mean_gross_bps": float(gross.mean() * 10000),
        "mean_net_bps": float(net.mean() * 10000),
        "net_win_rate": float((net > 0).mean()),
        "closed_trade_index_drawdown": float(1 - (equity / np.maximum.accumulate(equity)).min()),
        "daily_lower_net_bps": lower,
        "exit_reasons": outcomes.exit_reason.value_counts().to_dict(),
        "daily_average_net_bps": {str(day): float(value * 10000) for day, value in daily.items()},
    }


save(
    "registration.json",
    {
        "registered_at": datetime.now(UTC).isoformat(),
        "protocol": PROTOCOL,
        "protocol_sha256": sha(HERE / "protocol.json"),
        "analysis_sha256": sha(Path(__file__)),
        "actual_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "runtime": research_runtime_fingerprint(),
        "source_files": {str(p.relative_to(ROOT)): sha(p) for p in sorted((ROOT / "src").rglob("*.py"))},
        "config_sha256": sha(ROOT / "config/strategies.yaml"),
        "all_six_candidates_retained": True,
        "live_qualification": False,
    },
)

try:
    registry = build_strategy_registry(Settings.load(ROOT, mode="live").strategies.enabled)
    for name, parameters in PROTOCOL["strategies"].items():
        assert dict(registry.resolve(name).spec.parameters) == parameters
    interval = BarInterval(PROTOCOL["interval"])
    start = datetime.fromisoformat(PROTOCOL["warmup_start"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(PROTOCOL["end_exclusive"].replace("Z", "+00:00"))
    policy = BarrierPolicy(target_r=3, stop_r=2, maximum_bars=24, round_trip_cost_bps=34)
    context = StrategyContext.for_market("binance", "spot")
    results = []
    for symbol in PROTOCOL["symbols"]:
        print(f"Downloading verified archives: {symbol}", flush=True)
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            fetched = BinancePublicArchive(client, cache_dir=OUT / "archives").fetch(
                symbol=symbol, interval=interval, start=start, end=end
            )
        bars = fetched.bars.copy()
        save(f"{symbol}-source.json", {"manifest": fetched.manifest, "unavailable": fetched.unavailable})
        if bars.empty:
            raise ValueError(f"No verified archives available for {symbol}; no substituted data")
        save(f"{symbol}-bars.json", json.loads(bars.to_json(orient="records", date_format="iso")))
        bars["atr"] = gap_safe_atr(bars, period=14)
        source_invalid = sum(item.get("invalid_boundary_rows", 0) for item in fetched.manifest)
        for name in PROTOCOL["strategies"]:
            item = registry.resolve(name)
            causal_checks = []
            for fraction in (0.25, 0.5, 0.75):
                cut = int(len(bars) * fraction)
                causal_checks.append(
                    asdict(
                        audit_prefix_invariance(
                            item.spec, bars.iloc[:cut], bars, context, context, generator=item.generator
                        )
                    )
                )
            if not all(check["passed"] for check in causal_checks):
                raise ValueError(f"Causal prefix check failed: {symbol}/{name}")
            signals = _gap_safe_signals(item, bars, context)
            target_bps = bars.atr * 3 / bars.close * 10000
            rejected_short = int((signals.signal == -1).sum())
            rejected_cost = int(((signals.signal == 1) & (target_bps < 68)).sum())
            signals.loc[(signals.signal != 1) | (target_bps < 68), "signal"] = 0
            row = {
                "symbol": symbol,
                "strategy": name,
                "parameters": dict(item.spec.parameters),
                "prefix_checks": causal_checks,
                "short_signals_not_traded": rejected_short,
                "long_signals_below_cost_floor": rejected_cost,
                "periods": {},
            }
            for label, left, right in (
                ("screen", PROTOCOL["screen_start"], PROTOCOL["later_check_start"]),
                ("later_check", PROTOCOL["later_check_start"], PROTOCOL["end_exclusive"]),
            ):
                left, right = pd.Timestamp(left), pd.Timestamp(right)
                truncated = bars[bars.close_timestamp <= right].copy()
                decisions = signals.iloc[: len(truncated)].copy()
                decisions.loc[
                    (decisions.decision_timestamp < left) | (decisions.decision_timestamp >= right), "signal"
                ] = 0
                expected = pd.date_range(left, right, freq="15min", inclusive="left")
                observed = pd.DatetimeIndex(
                    bars.loc[(bars.open_timestamp >= left) & (bars.open_timestamp < right), "open_timestamp"]
                )
                quality = {
                    "expected_bars": len(expected),
                    "observed_bars": len(observed),
                    "missing_bars": len(expected.difference(observed)),
                    "duplicate_opens": int(observed.duplicated().sum()),
                    "invalid_boundary_rows_in_source": source_invalid,
                }
                scored = audit_strategy_opportunities(
                    truncated, decisions, policy, strategy_id=name, family=item.spec.family.value
                )
                outcomes = scored.outcomes
                save(
                    f"{symbol}-{name}-{label}-trades.json",
                    json.loads(outcomes.to_json(orient="records", date_format="iso")),
                )
                base, stress = metrics(outcomes, 34), metrics(outcomes, 68)
                reasons = []
                if base["trades"] < 100:
                    reasons.append("fewer_than_100_trades")
                if base["exit_days"] < 10:
                    reasons.append("fewer_than_10_exit_days")
                if base["mean_net_bps"] is None or base["mean_net_bps"] <= 0:
                    reasons.append("base_average_not_positive")
                if stress["mean_net_bps"] is None or stress["mean_net_bps"] <= 0:
                    reasons.append("stressed_average_not_positive")
                if stress["daily_lower_net_bps"] is None or stress["daily_lower_net_bps"] <= 0:
                    reasons.append("adjusted_daily_lower_bound_not_positive")
                if any(quality[k] for k in ("missing_bars", "duplicate_opens", "invalid_boundary_rows_in_source")):
                    reasons.append("incomplete_or_invalid_source")
                if any(scored.diagnostics[k] for k in ("gap_blocked", "gap_truncated", "right_censored")):
                    reasons.append("censored_outcomes_present")
                row["periods"][label] = {
                    "start": left.isoformat(),
                    "end_exclusive": right.isoformat(),
                    "quality": quality,
                    "diagnostics": scored.diagnostics,
                    "base": base,
                    "stress": stress,
                    "reasons": reasons,
                }
            row["screen_survivor"] = all(not period["reasons"] for period in row["periods"].values())
            results.append(row)
            save(f"{symbol}-{name}-result.json", row)
            print(
                json.dumps(
                    {
                        "symbol": symbol,
                        "strategy": name,
                        "survivor": row["screen_survivor"],
                        "later": row["periods"]["later_check"]["base"],
                    }
                ),
                flush=True,
            )
    save(
        "result.json",
        {
            "status": "completed",
            "protocol_sha256": sha(HERE / "protocol.json"),
            "candidates": results,
            "live_qualification": False,
            "drawdown_definition": (
                "Peak-to-trough closed-trade return index, assuming serial full-notional trades. "
                "Not an app account, marked-to-market or portfolio drawdown. No pooling overlapping candidates."
            ),
        },
    )
    save("completion.json", {"completed_at": datetime.now(UTC).isoformat(), "result_sha256": sha(OUT / "result.json")})
except Exception as error:
    save("failure.json", {"failed_at": datetime.now(UTC).isoformat(), "error": f"{type(error).__name__}: {error}"})
    raise
