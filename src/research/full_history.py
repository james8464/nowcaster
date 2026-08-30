from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx
import pandas as pd

from src.app_snapshot import build_app_snapshot, write_snapshot_atomic
from src.backtest.costs import CostAssumptions
from src.backtest.execution import ExecutionAssumptions
from src.config.settings import Settings
from src.database.engine import Database
from src.database.schema import TABLES
from src.ingestion.bars import (
    INTERVAL_DURATION,
    BarProvider,
    BarRequest,
    MarketBar,
    atomic_write_bytes,
    request_with_retries,
)
from src.ingestion.binance_bars import BinanceBarProvider
from src.strategies.ensemble import EnsembleConfig
from src.strategies.library import build_strategy_registry
from src.strategies.pipeline import (
    BarProviderName,
    EvaluationOptions,
    IngestOptions,
    LearningOptions,
    StageOutcome,
    StrategyPipeline,
    StrategyScope,
)
from src.strategies.types import BarInterval, StrategyMode, canonical_hash
from src.utils.provenance import research_source_hash

CI_CUTOFF = datetime(2026, 8, 20, tzinfo=UTC)
CI_BAR_COUNT = 110
SYMBOL_MAP = {"BTCUSDT": "BTCUSDT", "ETHUSDT": "ETHUSDT"}
EQUITY_SESSION_ONLY = {"opening_range_breakout", "etf_last_half_hour_momentum"}
PAIRED_CONTEXT = {"rolling_cointegration_pairs"}
CROSS_SECTIONAL_CONTEXT = {"crypto_cross_sectional_momentum"}


class DeterministicResearchProvider(BarProvider):
    """Small, generated, network-free bar fixture described by a committed manifest."""

    def __init__(self, cutoff: datetime, bar_count: int):
        self.cutoff = cutoff
        self.bar_count = bar_count

    def fetch(self, request: BarRequest) -> Iterable[MarketBar]:
        duration = INTERVAL_DURATION[request.interval]
        first = self.cutoff - duration * self.bar_count
        seed = sum(ord(character) for character in request.symbol) + int(duration.total_seconds())
        base = 60_000.0 if request.symbol.startswith("BTC") else 3_000.0
        bars: list[MarketBar] = []
        for index in range(self.bar_count):
            opened_at = first + duration * index
            if opened_at < request.start or opened_at >= request.end:
                continue
            previous = base * (1 + 0.0008 * index + 0.012 * math.sin((index + seed) / 7))
            close = base * (1 + 0.0008 * (index + 1) + 0.012 * math.sin((index + 1 + seed) / 7))
            high = max(previous, close) * (1.0015 + (index % 5) * 0.0001)
            low = min(previous, close) * (0.9985 - (index % 3) * 0.0001)
            volume = 100 + (index % 17) * 7 + 25 * (1 + math.sin((index + seed) / 11))
            raw = {
                "fixture": "intraday-strategy-research-v1",
                "symbol": request.symbol,
                "interval": request.interval.value,
                "open_timestamp": opened_at,
                "open": round(previous, 8),
                "high": round(high, 8),
                "low": round(low, 8),
                "close": round(close, 8),
                "volume": round(volume, 8),
            }
            closed_at = opened_at + duration
            bars.append(
                MarketBar(
                    provider="csv",
                    feed=request.feed or "ci-fixture",
                    symbol=request.symbol,
                    interval=request.interval,
                    open_timestamp=opened_at,
                    close_timestamp=closed_at,
                    available_at=closed_at,
                    retrieved_at=self.cutoff,
                    revision=1,
                    finalized=True,
                    open=raw["open"],
                    high=raw["high"],
                    low=raw["low"],
                    close=raw["close"],
                    volume=raw["volume"],
                    vwap=round((raw["high"] + raw["low"] + raw["close"]) / 3, 8),
                    trade_count=100 + index,
                    payload_hash=canonical_hash(raw),
                )
            )
        return bars


def _execution_assumptions() -> ExecutionAssumptions:
    return ExecutionAssumptions(
        costs=CostAssumptions(
            taker_fee_bps=10,
            half_spread_bps=2,
            slippage_bps=5,
            funding_bps_per_period=0,
            borrow_bps_per_period=0,
        ),
        latency=timedelta(milliseconds=250),
        lot_size=0.000001,
        participation_rate=0.05,
        short_borrow_available=False,
    )


def _pipeline(
    settings: Settings,
    database: Database,
    provider: BarProvider,
    provider_name: BarProviderName,
    cutoff: datetime,
) -> StrategyPipeline:
    family_caps = settings.strategies.family_weight_caps
    ensemble = EnsembleConfig(
        maximum_strategy_weight=settings.strategies.strategy_weight_cap,
        maximum_family_weight=max(family_caps.values()),
        family_weight_caps=family_caps,
    )
    return StrategyPipeline(
        database,
        build_strategy_registry(settings.strategies.enabled),
        {provider_name: provider},
        clock=lambda: cutoff,
        ensemble_config=ensemble,
        execution_assumptions=_execution_assumptions(),
    ).bind_settings(settings)


def _ensure_clean_research_database(database: Database) -> None:
    contaminated = [
        table_name
        for table_name in sorted(TABLES)
        if table_name != "schema_versions" and int(database.scalar(f"select count(*) from {table_name}") or 0) > 0
    ]
    if contaminated:
        raise ValueError(
            "research requires a clean isolated database; existing rows found in: " + ", ".join(contaminated)
        )


def _evaluate_with_failure_isolation(pipeline: StrategyPipeline, scope: StrategyScope) -> StageOutcome:
    try:
        return pipeline.evaluate(EvaluationOptions(scope=scope))
    except Exception as cohort_error:
        successful_ids: list[str] = []
        failures: list[str] = []
        for strategy_id in scope.strategy_ids:
            isolated_scope = scope.model_copy(update={"strategy_ids": (strategy_id,)})
            try:
                outcome = pipeline.evaluate(EvaluationOptions(scope=isolated_scope))
            except Exception as error:
                failures.append(f"{strategy_id}: {type(error).__name__}: {str(error)[:300]}")
                continue
            if outcome.status in {"completed", "reused"}:
                successful_ids.append(strategy_id)
            else:
                failures.append(f"{strategy_id}: {outcome.message}")
        if successful_ids:
            survivor_scope = scope.model_copy(update={"strategy_ids": tuple(successful_ids)})
            rebuilt = pipeline.evaluate(EvaluationOptions(scope=survivor_scope))
            return StageOutcome(
                rebuilt.status,
                f"rebuilt {len(successful_ids)} successful survivors after cohort failure; failures={failures}; "
                f"{rebuilt.message}",
                dataset_hash=rebuilt.dataset_hash,
                strategy_run_id=rebuilt.strategy_run_id,
                strategy_run_ids=rebuilt.strategy_run_ids,
            )
        message = f"no successful strategies after cohort failure; failures={failures or [str(cohort_error)[:300]]}"
        return StageOutcome("unavailable", message)


def _ensemble_policy(config: EnsembleConfig) -> dict[str, Any]:
    return {
        "equal_weight_shrinkage": config.equal_weight_shrinkage,
        "maximum_family_weight": config.maximum_family_weight,
        "maximum_strategy_weight": config.maximum_strategy_weight,
        "nonnegative": True,
    }


def _enabled_intervals(settings: Settings) -> tuple[BarInterval, ...]:
    crypto_intervals = {interval for spec in settings.strategies.enabled for interval in spec.intervals}
    order = {interval: position for position, interval in enumerate(BarInterval)}
    return tuple(sorted(crypto_intervals, key=order.__getitem__))


def _strategies_for_scope(settings: Settings, symbol: str, interval: BarInterval) -> tuple[str, ...]:
    selected: list[str] = []
    for spec in settings.strategies.enabled:
        if interval not in spec.intervals:
            continue
        strategy_id = spec.strategy_id
        if strategy_id in EQUITY_SESSION_ONLY | PAIRED_CONTEXT | CROSS_SECTIONAL_CONTEXT:
            continue
        if strategy_id == "bitcoin_active_session_momentum" and symbol != "BTCUSDT":
            continue
        selected.append(strategy_id)
    return tuple(selected)


def _scope(
    strategy_ids: tuple[str, ...],
    provider: BarProviderName,
    feed: str,
    symbol: str,
    interval: BarInterval,
    mode: StrategyMode = StrategyMode.PAPER,
) -> StrategyScope:
    return StrategyScope(
        strategy_ids=strategy_ids,
        provider=provider,
        feed=feed,
        symbol=symbol,
        interval=interval,
        mode=mode,
    )


def _semantic_snapshot_payload(snapshot: Any) -> dict[str, Any]:
    payload = snapshot.model_dump(mode="json")
    for receipt_field in ("generated_at", "last_refresh", "git_commit"):
        payload["metadata"].pop(receipt_field, None)
    return payload


def _source_hash(root: Path) -> str:
    return research_source_hash(root)


def _research_config_hash(settings: Settings) -> str:
    return canonical_hash(settings.research_config_hash_payload())


def _data_quality(database: Database, cutoff: datetime, intervals: tuple[BarInterval, ...]) -> dict[str, Any]:
    bars = database.frame("select * from market_bars order by symbol, interval, open_timestamp, revision")
    if bars.empty:
        return {
            "intended_grain": "one finalized provider/feed/symbol/interval/open_timestamp/revision record",
            "intervals": [interval.value for interval in intervals],
            "rows": 0,
            "duplicate_logical_bars": 0,
            "ohlcv_invalid_rows": 0,
            "coverage_gap_count": 0,
            "revision_rows": 0,
            "leakage_checks_passed": False,
            "sample_sufficiency": "unavailable",
            "downstream_risk": "No provider history was available, so no strategy evidence can be inferred.",
        }
    logical = ["provider", "feed", "symbol", "interval", "open_timestamp", "revision", "available_at"]
    duplicates = int(bars.duplicated(logical).sum())
    invalid_ohlcv = int(
        (
            (bars["high"] < bars[["open", "close"]].max(axis=1))
            | (bars["low"] > bars[["open", "close"]].min(axis=1))
            | (bars["high"] < bars["low"])
            | (bars["volume"] < 0)
        ).sum()
    )
    for column in ("open_timestamp", "close_timestamp", "available_at"):
        bars[column] = pd.to_datetime(bars[column], utc=True)
    timing_valid = bool(
        bars["finalized"].all()
        and (bars["available_at"] >= bars["close_timestamp"]).all()
        and (bars["close_timestamp"] <= pd.Timestamp(cutoff)).all()
    )
    ordered = bars.sort_values(["symbol", "interval", "open_timestamp"], kind="stable")
    returns = ordered.groupby(["symbol", "interval"], sort=True)["close"].pct_change(fill_method=None).dropna()
    median = float(returns.median()) if len(returns) else None
    mad = float((returns - median).abs().median()) if len(returns) and median is not None else None
    outliers = int(((returns - median).abs() > 10 * mad).sum()) if mad and median is not None else 0
    gaps = database.frame("select gaps from dataset_coverage_requests")
    gap_count = sum(len(item.get("missing", [])) for item in gaps.get("gaps", []) if isinstance(item, dict))
    audits = database.frame("select passed from causal_audits")
    executions = database.frame("select decision_timestamp, execution_timestamp from strategy_executions")
    execution_lag_valid = True
    if not executions.empty:
        decision = pd.to_datetime(executions["decision_timestamp"], utc=True)
        execution = pd.to_datetime(executions["execution_timestamp"], utc=True)
        execution_lag_valid = bool((execution > decision).all())
    sample_counts = bars.groupby(["symbol", "interval"], sort=True).size()
    return {
        "intended_grain": "one finalized provider/feed/symbol/interval/open_timestamp/revision record",
        "intervals": [interval.value for interval in intervals],
        "rows": int(len(bars)),
        "duplicate_logical_bars": duplicates,
        "ohlcv_invalid_rows": invalid_ohlcv,
        "utc_normalized": True,
        "finalization_and_cutoff_valid": timing_valid,
        "coverage_gap_count": int(gap_count),
        "revision_rows": int((bars["revision"] > 1).sum()),
        "freshness_cutoff": cutoff.isoformat().replace("+00:00", "Z"),
        "latest_close": bars["close_timestamp"].max().isoformat().replace("+00:00", "Z"),
        "return_distribution": {
            "count": int(len(returns)),
            "mean": float(returns.mean()) if len(returns) else None,
            "standard_deviation": float(returns.std(ddof=1)) if len(returns) > 1 else None,
            "minimum": float(returns.min()) if len(returns) else None,
            "maximum": float(returns.max()) if len(returns) else None,
            "robust_outlier_count": outliers,
        },
        "volume_distribution": {
            "minimum": float(bars["volume"].min()),
            "median": float(bars["volume"].median()),
            "maximum": float(bars["volume"].max()),
        },
        "leakage_checks_passed": bool(
            timing_valid and execution_lag_valid and gap_count == 0 and not audits.empty and audits["passed"].all()
        ),
        "prefix_audits": int(len(audits)),
        "sample_sufficiency": {
            f"{symbol}/{interval}": int(value) for (symbol, interval), value in sample_counts.items()
        },
        "downstream_risk": (
            "Gaps, stale bars, revisions, invalid OHLCV, or time-travel would bias folds, fills, costs, "
            "weights, and promotion; any failed check makes the affected scope unavailable."
        ),
    }


def _unavailable_reason(strategy_id: str, crypto_count: int) -> str:
    if strategy_id in EQUITY_SESSION_ONLY:
        return "requires an equity/ETF instrument and exchange-session calendar; configured research assets are crypto"
    if strategy_id in PAIRED_CONTEXT:
        return (
            "requires authenticated point-in-time paired-bar context; "
            "the scalar full-history runner does not splice peers"
        )
    if strategy_id in CROSS_SECTIONAL_CONTEXT:
        return f"requires at least 5 point-in-time liquid crypto assets; only {crypto_count} are configured"
    return "no compatible configured scope produced evidence"


def _strategy_catalog(settings: Settings, database: Database) -> list[dict[str, Any]]:
    frame = database.frame(
        "select strategy_id, symbol, interval, status, metrics from strategy_runs "
        "order by strategy_id, symbol, interval, run_timestamp"
    )
    catalog: list[dict[str, Any]] = []
    crypto_count = sum(item.enabled and item.asset_class == "crypto" for item in settings.instruments.instruments)
    priority = {"evaluated": 4, "rejected": 3, "failed": 2, "unavailable": 1}
    for spec in settings.strategies.enabled:
        matches = frame.loc[frame["strategy_id"] == spec.strategy_id] if not frame.empty else frame
        scopes: list[dict[str, Any]] = []
        for row in matches.itertuples(index=False):
            metrics = row.metrics if isinstance(row.metrics, dict) else {}
            scopes.append(
                {
                    "symbol": str(row.symbol),
                    "interval": str(row.interval),
                    "status": str(row.status),
                    "reason": str(metrics.get("status_reason") or metrics.get("error_summary") or "recorded"),
                }
            )
        if scopes:
            status = max((item["status"] for item in scopes), key=lambda value: priority.get(value, 0))
            reason = next((item["reason"] for item in scopes if item["status"] == status), None)
        else:
            status = "unavailable"
            reason = _unavailable_reason(spec.strategy_id, crypto_count)
        entry: dict[str, Any] = {
            "strategy_id": spec.strategy_id,
            "family": spec.family.value,
            "status": status,
            "scopes": scopes,
        }
        if status in {"unavailable", "failed"}:
            entry["reason"] = reason or _unavailable_reason(spec.strategy_id, crypto_count)
        catalog.append(entry)
    return catalog


def _report(summary: dict[str, Any]) -> str:
    counts: dict[str, int] = {}
    for item in summary["strategy_catalog"]:
        counts[item["status"]] = counts.get(item["status"], 0) + 1
    return "\n".join(
        [
            "# Strategy research report",
            "",
            "This is a reproducible research/paper-trading artifact. Historical evidence is not live proof "
            "and does not promise profit.",
            "Missing history is unavailable evidence, never a successful result.",
            "",
            f"- Profile: {summary['profile']}",
            f"- Fixed UTC cutoff: {summary['cutoff']}",
            f"- Semantic snapshot hash: `{summary['semantic_snapshot_hash']}`",
            f"- Dataset hash: `{summary['dataset_hash']}`",
            f"- Config hash: `{summary['config_hash']}`",
            f"- Code hash: `{summary['code_hash']}`",
            f"- Strategy statuses: {json.dumps(counts, sort_keys=True)}",
            "",
            "The compact report omits raw provider bars. Review docs/research-results.md before interpreting "
            "any metric.",
            "",
        ]
    )


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_bytes(path, (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode())


def _ci_run(settings: Settings, database: Database, output_dir: Path) -> dict[str, Any]:
    cutoff = CI_CUTOFF
    provider = DeterministicResearchProvider(cutoff, CI_BAR_COUNT)
    pipeline = _pipeline(settings, database, provider, BarProviderName.CSV, cutoff)
    intervals = _enabled_intervals(settings)
    manifests: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    # One configured symbol is enough to exercise every scalar-compatible strategy in CI.
    # The live profile remains responsible for every configured provider mapping.
    for configured_symbol, provider_symbol in (("BTCUSDT", "BTCUSDT"),):
        if configured_symbol not in {item.symbol for item in settings.instruments.instruments if item.enabled}:
            continue
        for interval in intervals:
            strategy_ids = _strategies_for_scope(settings, provider_symbol, interval)
            if not strategy_ids:
                attempts.append(
                    {
                        "symbol": provider_symbol,
                        "interval": interval.value,
                        "status": "unavailable",
                        "reason": "no strategy meets this asset/session/universe context",
                    }
                )
                continue
            duration = INTERVAL_DURATION[interval]
            scope = _scope(strategy_ids, BarProviderName.CSV, "ci-fixture", provider_symbol, interval)
            start = cutoff - duration * CI_BAR_COUNT
            ingest = pipeline.ingest(IngestOptions(scope=scope, start=start, end=cutoff))
            evaluation = _evaluate_with_failure_isolation(pipeline, scope) if ingest.status != "unavailable" else ingest
            attempts.append(
                {
                    "symbol": provider_symbol,
                    "interval": interval.value,
                    "strategies": list(strategy_ids),
                    "ingest_status": ingest.status,
                    "evaluation_status": evaluation.status,
                    "reason": evaluation.message,
                }
            )
            if ingest.dataset_hash:
                manifests.append(
                    {
                        "symbol": provider_symbol,
                        "interval": interval.value,
                        "dataset_hash": ingest.dataset_hash,
                        "row_count": CI_BAR_COUNT,
                        "start": start.isoformat().replace("+00:00", "Z"),
                        "end": cutoff.isoformat().replace("+00:00", "Z"),
                    }
                )

    learning_scope = _scope(
        ("rsi_reversal",),
        BarProviderName.CSV,
        "ci-fixture",
        "BTCUSDT",
        BarInterval.FIVE_MINUTES,
        StrategyMode.WALK_FORWARD_LEARNING,
    )
    try:
        learning = pipeline.learn(LearningOptions(scope=learning_scope, evaluation_budget=2, seed=42))
        learning_record = {
            "status": learning.status,
            "reason": learning.message,
            "evaluated_candidates": learning.evaluated_candidates,
        }
    except ValueError as error:
        learning_record = {"status": "unavailable", "reason": str(error), "evaluated_candidates": 0}

    snapshot = build_app_snapshot(database, settings)
    snapshot = snapshot.model_copy(
        update={
            "metadata": snapshot.metadata.model_copy(
                update={"generated_at": cutoff, "git_commit": "deterministic-ci-fixture"}
            )
        }
    )
    snapshot_path = output_dir / "nowcaster-snapshot.json"
    write_snapshot_atomic(snapshot, snapshot_path)
    semantic_hash = canonical_hash(_semantic_snapshot_payload(snapshot))
    positive_components = [
        {
            "strategy_id": item.strategy_id,
            "family": item.family,
            "symbol": item.symbol,
            "interval": item.interval,
            "weight": item.weight,
        }
        for item in snapshot.ensemble_components
        if item.weight > 0
    ]
    fixture_manifest = json.loads(
        (settings.project_root / "data" / "demo" / "intraday" / "research-fixture.json").read_text(encoding="utf-8")
    )
    summary = {
        "schema_version": 1,
        "profile": "ci",
        "cutoff": cutoff.isoformat().replace("+00:00", "Z"),
        "source": "deterministic generated fixture; never substituted for live provider history",
        "provider_mapping": SYMBOL_MAP,
        "fixture_manifest_hash": canonical_hash(fixture_manifest),
        "dataset_hash": canonical_hash(manifests),
        "datasets": manifests,
        "config_hash": _research_config_hash(settings),
        "code_hash": _source_hash(settings.project_root),
        "semantic_snapshot_hash": semantic_hash,
        "strategy_catalog": _strategy_catalog(settings, database),
        "ensemble_policy": _ensemble_policy(pipeline.ensemble_config),
        "ensemble_components": positive_components,
        "learning_benchmark": learning_record,
        "attempts": attempts,
        "data_quality": _data_quality(database, cutoff, intervals),
        "snapshot_counts": {
            "strategies": len(snapshot.strategies),
            "ensemble_components": len(snapshot.ensemble_components),
            "learning_runs": len(snapshot.learning_runs),
            "causal_audits": len(snapshot.causal_audits),
        },
        "limitations": [
            "Deterministic demo bars test the pipeline; they are not live evidence.",
            "Binance spot pairs are USDT quoted and venue-specific, not composite USD prices.",
            "Backtests can overfit and materially understate live costs and operational failures.",
        ],
    }
    _write_json(output_dir / "research-summary.json", summary)
    atomic_write_bytes(output_dir / "strategy-research.md", _report(summary).encode())
    return summary


def _alpaca_probe() -> dict[str, Any]:
    key_present = bool(os.getenv("APCA_API_KEY_ID") or os.getenv("ALPACA_API_KEY"))
    secret_present = bool(os.getenv("APCA_API_SECRET_KEY") or os.getenv("ALPACA_API_SECRET"))
    usable = key_present and secret_present
    return {
        "status": "credentials_present_not_downloaded" if usable else "unavailable",
        "key_present": key_present,
        "secret_present": secret_present,
        "reason": (
            "credentials were present; use the scoped Alpaca strategy ingest command to test feed entitlement"
            if usable
            else "Alpaca credentials are absent; set APCA_API_KEY_ID and APCA_API_SECRET_KEY locally, never in Git"
        ),
    }


def _live_unavailable(settings: Settings, output_dir: Path, cutoff: datetime, reason: str) -> dict[str, Any]:
    intervals = _enabled_intervals(settings)
    catalog = [
        {
            "strategy_id": spec.strategy_id,
            "family": spec.family.value,
            "status": "unavailable",
            "reason": reason,
            "scopes": [],
        }
        for spec in settings.strategies.enabled
    ]
    summary = {
        "schema_version": 1,
        "profile": "live",
        "cutoff": cutoff.isoformat().replace("+00:00", "Z"),
        "source": "Binance official spot REST API/archive-compatible external cache",
        "provider_mapping": SYMBOL_MAP,
        "attempt_status": "unavailable",
        "unavailable_reason": reason,
        "attempted_coverage": [],
        "dataset_hash": None,
        "config_hash": _research_config_hash(settings),
        "code_hash": _source_hash(settings.project_root),
        "semantic_snapshot_hash": "unavailable",
        "strategy_catalog": catalog,
        "ensemble_components": [],
        "alpaca": _alpaca_probe(),
        "data_quality": {
            "intended_grain": "one finalized Binance spot symbol/interval/open timestamp bar",
            "intervals": [interval.value for interval in intervals],
            "rows": 0,
            "leakage_checks_passed": False,
            "downstream_risk": (
                "No live provider bars were available; strategy results are unavailable, not successful."
            ),
        },
    }
    _write_json(output_dir / "research-summary.json", summary)
    atomic_write_bytes(output_dir / "strategy-research.md", _report(summary).encode())
    return summary


def _cache_probe(cache_dir: Path, symbol: str, interval: BarInterval, cutoff: datetime, payload: bytes) -> str:
    digest = hashlib.sha256(payload).hexdigest()
    stem = cutoff.strftime("earliest-through-%Y%m%dT%H%M%SZ")
    parent = cache_dir / "binance" / "spot" / symbol / interval.value
    atomic_write_bytes(parent / f"{stem}.json", payload)
    atomic_write_bytes(parent / f"{stem}.sha256", (digest + "\n").encode("ascii"))
    return digest


def _cached_probe(cache_dir: Path, symbol: str, interval: BarInterval, cutoff: datetime) -> tuple[bytes, str] | None:
    stem = cutoff.strftime("earliest-through-%Y%m%dT%H%M%SZ")
    parent = cache_dir / "binance" / "spot" / symbol / interval.value
    payload_path = parent / f"{stem}.json"
    checksum_path = parent / f"{stem}.sha256"
    if not payload_path.exists() or not checksum_path.exists():
        return None
    payload = payload_path.read_bytes()
    observed = hashlib.sha256(payload).hexdigest()
    expected = checksum_path.read_text(encoding="ascii").strip()
    if observed != expected:
        raise ValueError(f"cached Binance earliest-probe checksum mismatch: {payload_path}")
    return payload, observed


def _earliest_binance_bar(
    client: httpx.Client,
    cache_dir: Path,
    symbol: str,
    interval: BarInterval,
    cutoff: datetime,
) -> tuple[datetime, str]:
    cached = _cached_probe(cache_dir, symbol, interval, cutoff)
    if cached is None:
        response = request_with_retries(
            client,
            "GET",
            "https://api.binance.com/api/v3/klines",
            params={
                "symbol": symbol,
                "interval": interval.value,
                "startTime": 0,
                "endTime": int(cutoff.timestamp() * 1_000) - 1,
                "limit": 1,
            },
            max_attempts=3,
        )
        payload = response.content
        checksum = _cache_probe(cache_dir, symbol, interval, cutoff, payload)
    else:
        payload, checksum = cached
    decoded = json.loads(payload)
    if not isinstance(decoded, list) or not decoded or not isinstance(decoded[0], list) or len(decoded[0]) != 12:
        raise ValueError("Binance returned no documented finalized kline for the requested scope")
    opened_at = datetime.fromtimestamp(int(decoded[0][0]) / 1_000, UTC)
    return opened_at, checksum


def _external_cache_manifest(cache_dir: Path) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    for payload_path in sorted(cache_dir.rglob("*.json")):
        checksum_path = payload_path.with_suffix(".sha256")
        payload = payload_path.read_bytes()
        observed = hashlib.sha256(payload).hexdigest()
        expected = checksum_path.read_text(encoding="ascii").strip() if checksum_path.exists() else None
        files.append(
            {
                "path": payload_path.relative_to(cache_dir).as_posix(),
                "bytes": len(payload),
                "sha256": observed,
                "checksum_verified": observed == expected,
            }
        )
    return {
        "location": "external_to_repository",
        "file_count": len(files),
        "files": files,
        "manifest_hash": canonical_hash(files),
    }


def _live_run(
    settings: Settings,
    database: Database,
    output_dir: Path,
    cache_dir: Path,
    cutoff: datetime,
    max_chunks_per_scope: int | None,
) -> dict[str, Any]:
    cache_dir = cache_dir.expanduser().resolve()
    if cache_dir == settings.project_root or settings.project_root in cache_dir.parents:
        raise ValueError("live bulk cache must be outside the repository")
    cache_dir.mkdir(parents=True, exist_ok=True)
    intervals = _enabled_intervals(settings)
    attempts: list[dict[str, Any]] = []
    manifests: list[dict[str, Any]] = []
    completed_scopes: list[StrategyScope] = []
    with httpx.Client(timeout=30) as client:
        provider = BinanceBarProvider(client, cache_dir=cache_dir, clock=lambda: cutoff)
        pipeline = _pipeline(settings, database, provider, BarProviderName.BINANCE, cutoff)
        enabled_symbols = {item.symbol for item in settings.instruments.instruments if item.enabled}
        for configured_symbol, provider_symbol in sorted(SYMBOL_MAP.items()):
            if configured_symbol not in enabled_symbols:
                continue
            for interval in intervals:
                strategy_ids = _strategies_for_scope(settings, provider_symbol, interval)
                ingest_strategy_ids = strategy_ids or tuple(
                    spec.strategy_id for spec in settings.strategies.enabled if interval in spec.intervals
                )
                if not ingest_strategy_ids:
                    attempts.append(
                        {
                            "configured_symbol": configured_symbol,
                            "provider_symbol": provider_symbol,
                            "interval": interval.value,
                            "status": "unavailable",
                            "attempted_start": None,
                            "attempted_end": cutoff.isoformat().replace("+00:00", "Z"),
                            "reason": "asset/session/universe requirements are unmet",
                        }
                    )
                    continue
                scope = _scope(ingest_strategy_ids, BarProviderName.BINANCE, "spot", provider_symbol, interval)
                try:
                    earliest, probe_checksum = _earliest_binance_bar(
                        client, cache_dir, provider_symbol, interval, cutoff
                    )
                except Exception as error:
                    attempts.append(
                        {
                            "configured_symbol": configured_symbol,
                            "provider_symbol": provider_symbol,
                            "interval": interval.value,
                            "status": "unavailable",
                            "attempted_start": "provider-earliest",
                            "attempted_end": cutoff.isoformat().replace("+00:00", "Z"),
                            "reason": f"{type(error).__name__}: {str(error)[:500]}",
                        }
                    )
                    continue
                cursor = earliest
                complete = True
                chunk_count = 0
                while cursor < cutoff:
                    if max_chunks_per_scope is not None and chunk_count >= max_chunks_per_scope:
                        attempts.append(
                            {
                                "configured_symbol": configured_symbol,
                                "provider_symbol": provider_symbol,
                                "interval": interval.value,
                                "status": "unavailable",
                                "attempted_start": cursor.isoformat().replace("+00:00", "Z"),
                                "attempted_end": cutoff.isoformat().replace("+00:00", "Z"),
                                "reason": (
                                    "diagnostic chunk limit reached; remaining live history was not downloaded "
                                    "and no research result was inferred"
                                ),
                            }
                        )
                        complete = False
                        break
                    chunk_end = min(cursor + timedelta(days=30), cutoff)
                    try:
                        outcome = pipeline.ingest(IngestOptions(scope=scope, start=cursor, end=chunk_end))
                        status = outcome.status
                        reason = outcome.message
                        complete = complete and status in {"completed", "reused"}
                        if outcome.dataset_hash:
                            manifests.append(
                                {
                                    "configured_symbol": configured_symbol,
                                    "provider_symbol": provider_symbol,
                                    "quote_asset": "USDT",
                                    "interval": interval.value,
                                    "start": cursor.isoformat().replace("+00:00", "Z"),
                                    "end": chunk_end.isoformat().replace("+00:00", "Z"),
                                    "dataset_hash": outcome.dataset_hash,
                                    "earliest_probe_sha256": probe_checksum,
                                }
                            )
                    except Exception as error:
                        status = "unavailable"
                        reason = f"{type(error).__name__}: {str(error)[:500]}"
                        complete = False
                    attempts.append(
                        {
                            "configured_symbol": configured_symbol,
                            "provider_symbol": provider_symbol,
                            "interval": interval.value,
                            "status": status,
                            "attempted_start": cursor.isoformat().replace("+00:00", "Z"),
                            "attempted_end": chunk_end.isoformat().replace("+00:00", "Z"),
                            "reason": reason,
                        }
                    )
                    cursor = chunk_end
                    chunk_count += 1
                if complete and strategy_ids:
                    evaluation_scope = scope.model_copy(update={"strategy_ids": strategy_ids})
                    evaluation = _evaluate_with_failure_isolation(pipeline, evaluation_scope)
                    attempts.append(
                        {
                            "configured_symbol": configured_symbol,
                            "provider_symbol": provider_symbol,
                            "interval": interval.value,
                            "status": evaluation.status,
                            "stage": "evaluate",
                            "attempted_start": earliest.isoformat().replace("+00:00", "Z"),
                            "attempted_end": cutoff.isoformat().replace("+00:00", "Z"),
                            "reason": evaluation.message,
                        }
                    )
                    if evaluation.status != "unavailable":
                        completed_scopes.append(evaluation_scope)
                elif complete:
                    attempts.append(
                        {
                            "configured_symbol": configured_symbol,
                            "provider_symbol": provider_symbol,
                            "interval": interval.value,
                            "status": "unavailable",
                            "stage": "evaluate",
                            "attempted_start": earliest.isoformat().replace("+00:00", "Z"),
                            "attempted_end": cutoff.isoformat().replace("+00:00", "Z"),
                            "reason": "asset/session/universe requirements are unmet",
                        }
                    )

        learning_record: dict[str, Any] = {
            "status": "unavailable",
            "reason": "no complete BTCUSDT 5m provider scope was available",
            "evaluated_candidates": 0,
        }
        learning_base = next(
            (
                item
                for item in completed_scopes
                if item.symbol == "BTCUSDT"
                and item.interval == BarInterval.FIVE_MINUTES
                and "rsi_reversal" in item.strategy_ids
            ),
            None,
        )
        if learning_base is not None:
            learning_scope = learning_base.model_copy(
                update={"strategy_ids": ("rsi_reversal",), "mode": StrategyMode.WALK_FORWARD_LEARNING}
            )
            try:
                outcome = pipeline.learn(LearningOptions(scope=learning_scope, evaluation_budget=20, seed=42))
                learning_record = {
                    "status": outcome.status,
                    "reason": outcome.message,
                    "evaluated_candidates": outcome.evaluated_candidates,
                }
            except Exception as error:
                learning_record = {
                    "status": "unavailable",
                    "reason": f"{type(error).__name__}: {str(error)[:500]}",
                    "evaluated_candidates": 0,
                }

    snapshot = build_app_snapshot(database, settings)
    snapshot = snapshot.model_copy(update={"metadata": snapshot.metadata.model_copy(update={"generated_at": cutoff})})
    write_snapshot_atomic(snapshot, output_dir / "nowcaster-snapshot.json")
    semantic_hash = canonical_hash(_semantic_snapshot_payload(snapshot))
    catalog = _strategy_catalog(settings, database)
    provider_failures = [item["reason"] for item in attempts if item["status"] == "unavailable"]
    common_failure = provider_failures[0] if provider_failures else None
    if common_failure:
        for item in catalog:
            contextual = EQUITY_SESSION_ONLY | PAIRED_CONTEXT | CROSS_SECTIONAL_CONTEXT
            if not item["scopes"] and item["strategy_id"] not in contextual:
                item["reason"] = common_failure
    positive_components = [
        {
            "strategy_id": item.strategy_id,
            "family": item.family,
            "symbol": item.symbol,
            "interval": item.interval,
            "weight": item.weight,
        }
        for item in snapshot.ensemble_components
        if item.weight > 0
    ]
    quality = _data_quality(database, cutoff, intervals)
    summary = {
        "schema_version": 1,
        "profile": "live",
        "cutoff": cutoff.isoformat().replace("+00:00", "Z"),
        "source": "official Binance spot REST API with external checksummed resumable page cache",
        "provider_mapping": SYMBOL_MAP,
        "quote_disclosure": "BTCUSDT and ETHUSDT are Binance venue-specific USDT spot pairs, not composite USD data",
        "attempt_status": "completed" if completed_scopes else "unavailable",
        "unavailable_reason": None if completed_scopes else (common_failure or "no complete provider scope"),
        "attempted_coverage": attempts,
        "datasets": manifests,
        "cache_manifest": _external_cache_manifest(cache_dir),
        "dataset_hash": canonical_hash(manifests) if manifests else None,
        "config_hash": _research_config_hash(settings),
        "code_hash": _source_hash(settings.project_root),
        "semantic_snapshot_hash": semantic_hash,
        "strategy_catalog": catalog,
        "ensemble_components": positive_components,
        "ensemble_policy": _ensemble_policy(pipeline.ensemble_config),
        "learning_benchmark": learning_record,
        "alpaca": _alpaca_probe(),
        "data_quality": quality,
        "snapshot_counts": {
            "strategies": len(snapshot.strategies),
            "ensemble_components": len(snapshot.ensemble_components),
            "learning_runs": len(snapshot.learning_runs),
            "causal_audits": len(snapshot.causal_audits),
        },
    }
    _write_json(output_dir / "research-summary.json", summary)
    atomic_write_bytes(output_dir / "strategy-research.md", _report(summary).encode())
    return summary


def run_full_strategy_research(
    settings: Settings,
    *,
    database_url: str,
    output_dir: Path,
    profile: Literal["ci", "live"],
    cache_dir: Path | None = None,
    cutoff: datetime | None = None,
    max_chunks_per_scope: int | None = None,
) -> dict[str, Any]:
    """Run the deterministic CI profile or publish an honest live-provider attempt."""

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    database = Database.from_url(database_url)
    database.initialize()
    _ensure_clean_research_database(database)
    if profile == "ci":
        if max_chunks_per_scope is not None:
            raise ValueError("max_chunks_per_scope applies only to the live profile")
        return _ci_run(settings, database, output_dir)
    selected_cutoff = cutoff or datetime(2026, 8, 24, tzinfo=UTC)
    if selected_cutoff.tzinfo is not UTC:
        raise ValueError("research cutoff must be an explicit UTC datetime")
    if cache_dir is None:
        return _live_unavailable(
            settings,
            output_dir,
            selected_cutoff,
            "live full-history download requires an explicit external --cache-dir; demo data was not substituted",
        )
    if max_chunks_per_scope is not None and max_chunks_per_scope < 1:
        raise ValueError("max_chunks_per_scope must be positive")
    return _live_run(
        settings,
        database,
        output_dir,
        cache_dir,
        selected_cutoff,
        max_chunks_per_scope,
    )


__all__ = ["run_full_strategy_research"]
