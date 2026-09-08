"""Bounded, retrospective longer-horizon search for paper-study candidates."""

from __future__ import annotations

import hashlib
import math
import platform
from collections.abc import Mapping
from importlib import metadata
from pathlib import Path
from typing import Any

import pandas as pd

from src.backtest.opportunities import audit_strategy_opportunities
from src.models.trade_outcomes import BarrierPolicy
from src.research.opportunity_audit import gap_safe_atr
from src.strategies.library import StrategyContext
from src.strategies.registry import StrategyRegistry
from src.strategies.types import BarInterval, canonical_hash

STRATEGY_IDS = (
    "bollinger_keltner_squeeze",
    "macd_histogram_trend",
    "volatility_scaled_trend",
)
ROUND_TRIP_COST_BPS = 34
STRESSED_ROUND_TRIP_COST_BPS = 68
MINIMUM_TARGET_DISTANCE_BPS = 68
_PROSPECTIVE_MODULES = {
    Path("src/research/prospective.py"),
    Path("src/research/prospective_types.py"),
    Path("src/research/prospective_statistics.py"),
    Path("src/research/prospective_runtime.py"),
}
_RUNTIME_DISTRIBUTIONS = (
    ("numpy", "numpy"),
    ("pandas", "pandas"),
    ("pydantic", "pydantic"),
    ("scipy", "scipy"),
    ("statsmodels", "statsmodels"),
    ("scikit_learn", "scikit-learn"),
    ("pyyaml", "PyYAML"),
    ("httpx", "httpx"),
    ("websockets", "websockets"),
    ("certifi", "certifi"),
)


def research_runtime_fingerprint() -> dict[str, str]:
    """Return the explicit calculation and transport runtime versions."""

    result = {"python": platform.python_version()}
    for key, distribution in _RUNTIME_DISTRIBUTIONS:
        try:
            result[key] = metadata.version(distribution)
        except metadata.PackageNotFoundError as error:
            raise ValueError(f"required research runtime package is missing: {distribution}") from error
    return result


def discovery_source_hash(root: Path) -> str:
    """Hash the frozen development-search source scope with relative paths."""

    resolved = root.resolve()
    paths = set((resolved / "src").glob("**/*.py"))
    paths.difference_update(resolved / path for path in _PROSPECTIVE_MODULES)
    paths.update((resolved / "config").glob("*.yaml"))
    paths.update((resolved / name) for name in ("pyproject.toml", "scripts/search_holding_periods.py"))
    missing = [path for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"discovery source scope has missing files: {[str(path) for path in sorted(missing)]}")
    digest = hashlib.sha256()
    runtime_hash = canonical_hash(research_runtime_fingerprint()).encode()
    digest.update(len(runtime_hash).to_bytes(8, "big"))
    digest.update(runtime_hash)
    for path in sorted(paths, key=lambda item: item.relative_to(resolved).as_posix()):
        relative = path.relative_to(resolved).as_posix().encode()
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _summary(outcomes: pd.DataFrame) -> dict[str, Any]:
    if outcomes.empty:
        return {
            "trades": 0,
            "losses": 0,
            "mean_net_return": None,
            "mean_stressed_return": None,
        }
    gross = pd.to_numeric(outcomes["gross_return"], errors="raise")
    net = gross - ROUND_TRIP_COST_BPS / 10_000
    stressed = gross - STRESSED_ROUND_TRIP_COST_BPS / 10_000
    return {
        "trades": len(outcomes),
        "losses": int((stressed < 0).sum()),
        "mean_net_return": float(net.mean()),
        "mean_stressed_return": float(stressed.mean()),
    }


def _folds(
    bars: pd.DataFrame,
    signals: pd.DataFrame,
    policy: BarrierPolicy,
    *,
    strategy_id: str,
    family: str,
) -> list[dict[str, Any]]:
    boundaries = [int(len(bars) * index / 4) for index in range(5)]
    summaries: list[dict[str, Any]] = []
    for fold in range(4):
        start = pd.Timestamp(bars.iloc[boundaries[fold]]["open_timestamp"])
        end = (
            pd.Timestamp(bars.iloc[boundaries[fold + 1]]["open_timestamp"])
            if fold < 3
            else pd.Timestamp(bars.iloc[-1]["close_timestamp"])
        )
        fold_signals = signals.copy()
        decisions = pd.to_datetime(fold_signals["decision_timestamp"], utc=True)
        active = (decisions >= start) & (decisions < end)
        fold_signals.loc[~active, "signal"] = 0
        fold_signals.loc[~active, "strength"] = 0.0
        truncated_bars = bars.iloc[: boundaries[fold + 1]].copy() if fold < 3 else bars
        truncated_signals = fold_signals.iloc[: len(truncated_bars)].copy()
        outcomes = audit_strategy_opportunities(
            truncated_bars,
            truncated_signals,
            policy,
            strategy_id=strategy_id,
            family=family,
        ).outcomes
        summaries.append({"fold": fold + 1, "start": start.isoformat(), "end": end.isoformat(), **_summary(outcomes)})
    return summaries


def _rank_value(value: Any) -> float:
    if value is None:
        return -math.inf
    result = float(value)
    return result if math.isfinite(result) else -math.inf


def _select_candidate(configurations: list[dict[str, Any]]) -> dict[str, Any]:
    """Choose the deterministic configuration with the strongest worst fold."""

    if not configurations:
        raise ValueError("candidate selection requires at least one configuration")
    return max(
        configurations,
        key=lambda row: (
            min(_rank_value(fold["mean_stressed_return"]) for fold in row["folds"]),
            _rank_value(row["full"]["mean_stressed_return"]),
            str(row["candidate_id"]),
        ),
    )


def _finite_optional(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"discovery {field} must be numeric or null")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"discovery {field} must be numeric or null") from error
    if not math.isfinite(result):
        raise ValueError(f"discovery {field} must be finite")
    return result


def _count(value: Any, *, field: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"discovery {field} must be a non-negative integer")
    return value


def _configured_definition_hashes(root: Path) -> dict[str, str]:
    from src.config.settings import Settings
    from src.strategies.library import build_strategy_registry

    registry = build_strategy_registry(Settings.load(root.resolve(), mode="live").strategies.enabled)
    configured = {item.spec.strategy_id: item.spec.definition_hash for item in registry.enabled()}
    missing = set(STRATEGY_IDS) - set(configured)
    if missing:
        raise ValueError(f"configured discovery strategies are missing: {sorted(missing)}")
    return {strategy_id: configured[strategy_id] for strategy_id in STRATEGY_IDS}


def _validate_metrics(metrics: Any, *, field: str) -> None:
    if not isinstance(metrics, dict):
        raise ValueError(f"discovery {field} must be an object")
    mean_fields = {"mean_net_return", "mean_stressed_return"}
    if not mean_fields.issubset(metrics):
        raise ValueError(f"discovery {field} metrics must include both return means")
    trades = _count(metrics.get("trades"), field=f"{field}.trades")
    losses = _count(metrics.get("losses"), field=f"{field}.losses")
    net = _finite_optional(metrics.get("mean_net_return"), field=f"{field}.mean_net_return")
    stressed = _finite_optional(metrics.get("mean_stressed_return"), field=f"{field}.mean_stressed_return")
    if losses > trades or (trades == 0 and (net is not None or stressed is not None)):
        raise ValueError(f"discovery {field} metrics are internally inconsistent")
    if trades > 0 and (net is None or stressed is None):
        raise ValueError(f"discovery {field} metrics are internally inconsistent")
    if net is not None and stressed is not None and not math.isclose(net - stressed, 0.0034, rel_tol=0, abs_tol=1e-12):
        raise ValueError(f"discovery {field} metrics are internally inconsistent")


def _validate_trial(row: Any, *, definition_hashes: dict[str, str]) -> None:
    if not isinstance(row, dict):
        raise ValueError("discovery trial must be an object")
    definition = {
        "symbol": row.get("symbol"),
        "strategy_id": row.get("strategy_id"),
        "strategy_definition_hash": row.get("strategy_definition_hash"),
        "stop_atr": row.get("stop_atr"),
        "target_atr": row.get("target_atr"),
        "maximum_bars": row.get("maximum_bars"),
    }
    strategy_id = definition["strategy_id"]
    if strategy_id not in definition_hashes or definition["strategy_definition_hash"] != definition_hashes[strategy_id]:
        raise ValueError("discovery strategy definition hash does not match configured source")
    if row.get("candidate_id") != canonical_hash(definition):
        raise ValueError("discovery candidate ID is not canonical")
    folds = row.get("folds")
    if not isinstance(folds, list) or len(folds) != 4:
        raise ValueError("discovery trial must retain four chronological folds")
    previous_end: pd.Timestamp | None = None
    for ordinal, fold in enumerate(folds, start=1):
        if not isinstance(fold, dict) or fold.get("fold") != ordinal:
            raise ValueError("discovery folds must be ordered one through four")
        if not isinstance(fold.get("start"), str) or not isinstance(fold.get("end"), str):
            raise ValueError("discovery fold boundaries must be timestamp strings")
        start = pd.Timestamp(fold["start"])
        end = pd.Timestamp(fold["end"])
        invalid_chronology = (
            start.tzinfo is None
            or end.tzinfo is None
            or start >= end
            or (previous_end is not None and start != previous_end)
        )
        if invalid_chronology:
            raise ValueError("discovery folds must form one explicit chronological partition")
        previous_end = end
        _validate_metrics(fold, field=f"fold {ordinal}")
    _validate_metrics(row.get("full"), field="full")
    diagnostics = row.get("diagnostics")
    required_diagnostics = {
        "signals_considered",
        "overlap_blocked",
        "gap_blocked",
        "gap_truncated",
        "right_censored",
        "late_decision",
        "invalid_risk",
        "scored_opportunities",
        "minimum_target_distance_excluded",
    }
    if not isinstance(diagnostics, dict) or not required_diagnostics.issubset(diagnostics):
        raise ValueError("discovery trial diagnostics are incomplete")
    for name in required_diagnostics:
        _count(diagnostics[name], field=f"diagnostics.{name}")
    if diagnostics["scored_opportunities"] != row["full"]["trades"]:
        raise ValueError("discovery full trade count does not match diagnostics")
    expected_screen = all(
        fold["trades"] >= 30
        and (_finite_optional(fold["mean_stressed_return"], field="fold mean_stressed_return") or -math.inf) > 0
        for fold in folds
    )
    if type(row.get("screen_passed")) is not bool or row["screen_passed"] != expected_screen:
        raise ValueError("discovery screen_passed flag does not match four-fold evidence")


def validate_discovery(payload: dict, *, root: Path) -> None:
    """Validate local protocol integrity of a complete development discovery."""

    if not isinstance(payload, dict):
        raise ValueError("discovery payload must be an object")
    if payload.get("source_hash") != discovery_source_hash(root):
        raise ValueError("discovery source hash does not match local development source")
    if payload.get("runtime_fingerprint") != research_runtime_fingerprint():
        raise ValueError("discovery runtime fingerprint does not match local research runtime")
    if (
        payload.get("promotable") is not False
        or payload.get("status") != "experimental_observation_only"
        or payload.get("evidence_tier") != "retrospective_development_only"
        or payload.get("independent_validation") is not False
    ):
        raise ValueError("discovery must remain explicitly non-promotable development evidence")
    if payload.get("symbols") != ["BTCUSDT", "ETHUSDT"] or payload.get("interval") != "1h":
        raise ValueError("discovery universe does not match the predeclared scope")
    if payload.get("chronology") != {"kind": "four_chronological_development_folds", "folds": 4}:
        raise ValueError("discovery chronology does not match four development folds")
    assumptions = payload.get("assumptions")
    required_assumptions = {
        "round_trip_cost_bps": 34,
        "stressed_round_trip_cost_bps": 68,
        "minimum_target_distance_bps": 68,
        "entry": "next continuous hourly bar open",
        "ambiguous_barrier": "stop_first",
        "expiry": "expiry_before_target",
    }
    if not isinstance(assumptions, dict) or any(
        assumptions.get(key) != value for key, value in required_assumptions.items()
    ):
        raise ValueError("discovery execution and cost assumptions changed")
    manifests = payload.get("archive_manifests")
    if not isinstance(manifests, list) or not manifests:
        raise ValueError("discovery archive manifests must be retained")
    if payload.get("archive_manifest_hash") != canonical_hash(manifests):
        raise ValueError("discovery archive manifest hash mismatch")
    trials = payload.get("trials")
    if not isinstance(trials, list) or len(trials) != 24:
        raise ValueError("discovery must retain exactly 24 predeclared trials")
    definition_hashes = _configured_definition_hashes(root)
    for row in trials:
        _validate_trial(row, definition_hashes=definition_hashes)
    fold_partitions = {
        tuple((fold["start"], fold["end"]) for fold in row["folds"]) for row in trials if row["symbol"] == "BTCUSDT"
    }
    eth_partitions = {
        tuple((fold["start"], fold["end"]) for fold in row["folds"]) for row in trials if row["symbol"] == "ETHUSDT"
    }
    if len(fold_partitions) != 1 or len(eth_partitions) != 1:
        raise ValueError("discovery trials do not share one fold chronology per asset")
    expected_grid = {
        (symbol, strategy_id, stop_atr, 1.5 * stop_atr, maximum_bars)
        for symbol in ("BTCUSDT", "ETHUSDT")
        for strategy_id in STRATEGY_IDS
        for stop_atr in (1, 2)
        for maximum_bars in (6, 12)
    }
    observed_grid = {
        (row["symbol"], row["strategy_id"], row["stop_atr"], row["target_atr"], row["maximum_bars"]) for row in trials
    }
    candidate_ids = [row["candidate_id"] for row in trials]
    if observed_grid != expected_grid or len(observed_grid) != 24 or len(set(candidate_ids)) != 24:
        raise ValueError("discovery does not contain the exact unique 24-trial grid")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or len(candidates) != 2:
        raise ValueError("discovery must retain one selected candidate per asset")
    expected = [_select_candidate([row for row in trials if row["symbol"] == symbol]) for symbol in payload["symbols"]]
    if candidates != expected:
        raise ValueError("discovery selected candidates do not match deterministic ranking")


def _signal_hash(signals: pd.DataFrame, length: int) -> str:
    prefix = signals.iloc[:length]
    return canonical_hash(
        [
            {
                "decision_timestamp": pd.Timestamp(row["decision_timestamp"]).isoformat(),
                "data_through": pd.Timestamp(row["data_through"]).isoformat(),
                "signal": int(row["signal"]),
                "strength": float(row["strength"]),
            }
            for _, row in prefix.iterrows()
        ]
    )


def _generated_signals(bars: pd.DataFrame, strategy_id: str, registry: StrategyRegistry) -> pd.DataFrame:
    items = {item.spec.strategy_id: item for item in registry.enabled()}
    if strategy_id not in items:
        raise ValueError(f"strategy is not registered and enabled: {strategy_id}")
    item = items[strategy_id]
    context = StrategyContext.for_market(
        str(bars.iloc[0].get("provider", "unknown")), str(bars.iloc[0].get("feed", "unknown"))
    )
    opened = pd.to_datetime(bars["open_timestamp"], utc=True)
    previous_close = pd.to_datetime(bars["close_timestamp"], utc=True).shift(1)
    segments = opened.ne(previous_close).cumsum()
    frames = [
        item.generator(item.spec, segment.reset_index(drop=True).copy(), context).reset_index(drop=True)
        for _, segment in bars.groupby(segments, sort=False)
    ]
    signals = pd.concat(frames, ignore_index=True)
    if len(signals) != len(bars):
        raise ValueError("strategy generator must emit one row per bar")
    return signals


def _apply_long_eligibility(
    bars: pd.DataFrame, raw_signals: pd.DataFrame, *, target_atr: float
) -> tuple[pd.DataFrame, int]:
    distance_bps = (
        target_atr * pd.to_numeric(bars["atr"], errors="coerce") / pd.to_numeric(bars["close"], errors="raise") * 10_000
    )
    signals = raw_signals.copy()
    active = pd.to_numeric(signals["signal"], errors="raise").eq(1)
    target_eligible = distance_bps.ge(MINIMUM_TARGET_DISTANCE_BPS).fillna(False)
    excluded = active & ~target_eligible
    signals.loc[~(active & target_eligible), "signal"] = 0
    signals.loc[~(active & target_eligible), "strength"] = 0.0
    return signals, int(excluded.sum())


def eligible_long_signals(bars: pd.DataFrame, candidate: Mapping[str, Any], registry: StrategyRegistry) -> pd.DataFrame:
    """Generate the candidate's causal long signals with the frozen distance floor."""

    ordered = bars.sort_values("open_timestamp", kind="stable").reset_index(drop=True).copy()
    ordered["atr"] = gap_safe_atr(ordered, period=14)
    raw = _generated_signals(ordered, str(candidate["strategy_id"]), registry)
    eligible, _ = _apply_long_eligibility(ordered, raw, target_atr=float(candidate["target_atr"]))
    return eligible


def search_scope(bars: pd.DataFrame, *, symbol: str, registry: StrategyRegistry) -> dict[str, Any]:
    """Evaluate the twelve predeclared hourly configurations for one spot asset."""

    if len(bars) < 4:
        raise ValueError("holding-period search requires at least four bars")
    ordered = bars.sort_values("open_timestamp", kind="stable").reset_index(drop=True).copy()
    one_interval = ordered.get("interval", pd.Series(dtype=str)).astype(str).nunique() == 1
    if not one_interval or str(ordered.iloc[0]["interval"]) != "1h":
        raise ValueError("holding-period search requires one hourly scope")
    if ordered.get("symbol", pd.Series(dtype=str)).astype(str).str.upper().nunique() != 1:
        raise ValueError("holding-period search requires one symbol")
    normalized_symbol = symbol.strip().upper()
    if str(ordered.iloc[0]["symbol"]).upper() != normalized_symbol:
        raise ValueError("requested symbol does not match bars")
    ordered["atr"] = gap_safe_atr(ordered, period=14)

    registered = {item.spec.strategy_id: item for item in registry.enabled()}
    missing = set(STRATEGY_IDS) - set(registered)
    if missing:
        raise ValueError(f"holding-period registry is missing strategies: {sorted(missing)}")
    signals_by_strategy = {
        strategy_id: _generated_signals(ordered, strategy_id, registry) for strategy_id in STRATEGY_IDS
    }
    configurations: list[dict[str, Any]] = []
    prefix_length = int(len(ordered) * 0.75)
    for strategy_id in STRATEGY_IDS:
        item = registered[strategy_id]
        raw_signals = signals_by_strategy[strategy_id]
        if len(raw_signals) != len(ordered):
            raise ValueError("strategy generator must emit one row per bar")
        definition_hash = item.spec.definition_hash
        for stop_atr in (1, 2):
            target_atr = 1.5 * stop_atr
            for maximum_bars in (6, 12):
                definition = {
                    "symbol": normalized_symbol,
                    "strategy_id": strategy_id,
                    "strategy_definition_hash": definition_hash,
                    "stop_atr": stop_atr,
                    "target_atr": target_atr,
                    "maximum_bars": maximum_bars,
                }
                candidate_id = canonical_hash(definition)
                signals, excluded = _apply_long_eligibility(ordered, raw_signals, target_atr=target_atr)
                policy = BarrierPolicy(
                    target_r=target_atr,
                    stop_r=stop_atr,
                    maximum_bars=maximum_bars,
                    round_trip_cost_bps=ROUND_TRIP_COST_BPS,
                )
                audit = audit_strategy_opportunities(
                    ordered,
                    signals,
                    policy,
                    strategy_id=strategy_id,
                    family=item.spec.family.value,
                )
                folds = _folds(
                    ordered,
                    signals,
                    policy,
                    strategy_id=strategy_id,
                    family=item.spec.family.value,
                )
                configurations.append(
                    {
                        **definition,
                        "candidate_id": candidate_id,
                        "screen_passed": all(
                            fold["trades"] >= 30 and _rank_value(fold["mean_stressed_return"]) > 0 for fold in folds
                        ),
                        "folds": folds,
                        "full": _summary(audit.outcomes),
                        "diagnostics": {
                            **audit.diagnostics,
                            "minimum_target_distance_excluded": excluded,
                        },
                        "signal_prefix_hash": _signal_hash(raw_signals, prefix_length),
                    }
                )
    selected = _select_candidate(configurations)
    return {
        "symbol": normalized_symbol,
        "interval": BarInterval.ONE_HOUR.value,
        "configurations": configurations,
        "selected_candidate": selected,
        "promotable": False,
        "status": "experimental_observation_only",
        "assumptions": {
            "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
            "stressed_round_trip_cost_bps": STRESSED_ROUND_TRIP_COST_BPS,
            "minimum_target_distance_bps": MINIMUM_TARGET_DISTANCE_BPS,
            "entry": "next continuous hourly bar open",
            "ambiguous_barrier": "stop_first",
            "expiry": "expiry_before_target",
        },
    }


__all__ = [
    "discovery_source_hash",
    "eligible_long_signals",
    "research_runtime_fingerprint",
    "search_scope",
    "validate_discovery",
]
