from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import Any

import numpy as np
import pandas as pd

from src.app_snapshot.models import (
    AppSnapshot,
    BacktestPoint,
    BacktestSnapshot,
    CausalAuditSnapshot,
    DatasetCoverageSnapshot,
    DatasetGapSnapshot,
    DiscoveredRuleSnapshot,
    EarningsSnapshot,
    EnsembleComponentSnapshot,
    InstrumentSnapshot,
    LearningRunSnapshot,
    LearningTrialSnapshot,
    ModelDiagnosticSnapshot,
    OverviewSnapshot,
    PipelineRunSnapshot,
    PricePoint,
    QualityIssueSnapshot,
    ResearchSignalSnapshot,
    SensitivitySnapshot,
    SnapshotMetadata,
    StrategySnapshot,
)
from src.config.settings import Settings
from src.database.engine import Database
from src.ingestion.bars import INTERVAL_DURATION
from src.reporting.summary import research_statistics
from src.strategies.calendars import calendar_for
from src.strategies.types import BarInterval, canonical_hash
from src.utils.provenance import git_commit


def _finite(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _python_datetime(value: Any) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    converted = pd.Timestamp(value).to_pydatetime()
    return converted.replace(tzinfo=UTC) if converted.tzinfo is None else converted


def _python_date(value: Any) -> date:
    return pd.Timestamp(value).date()


def _metadata(database: Database, settings: Settings) -> SnapshotMetadata:
    latest = database.frame(
        "select mode, ended_at from pipeline_runs where status = 'success' order by ended_at desc limit 1"
    )
    raw_mode = str(latest.iloc[0]["mode"]) if not latest.empty else settings.mode
    data_mode = {"demo": "demo_real_snapshot", "live": "live_provider"}.get(raw_mode, raw_mode)
    expectation = database.scalar("select mode from consensus_estimates order by as_of_date desc limit 1")
    source_posture = (
        "Bundled real public snapshots with filing-date and expectation proxies"
        if data_mode == "demo_real_snapshot"
        else "Live configured providers; inspect source freshness and coverage before use"
    )
    providers = database.frame("select distinct provider, feed from market_bars order by provider, feed")
    if not providers.empty:
        data_mode = "strategy_provider_data"
        identities = ", ".join(f"{row.provider}/{row.feed}" for row in providers.itertuples(index=False))
        source_posture = f"Source-backed strategy bars: {identities}"
    return SnapshotMetadata(
        generated_at=datetime.now(UTC),
        git_commit=git_commit(settings.project_root),
        data_mode=data_mode,
        source_posture=source_posture,
        expectation_mode=str(expectation or "unavailable"),
        last_refresh=_python_datetime(latest.iloc[0]["ended_at"]) if not latest.empty else None,
    )


def _instruments(database: Database) -> list[InstrumentSnapshot]:
    companies = database.frame("select company_id, ticker, name from companies order by ticker")
    configured = database.frame(
        "select instrument_id, symbol, name, asset_class from instruments where enabled = true order by symbol"
    )
    prices = database.frame(
        "select symbol, trading_date, adjusted_close, volume from market_prices_daily order by symbol, trading_date"
    )
    company_lookup = {
        str(row.ticker): (str(row.company_id), str(row.name), "equity") for row in companies.itertuples(index=False)
    }
    configured_lookup = {
        str(row.symbol): (str(row.instrument_id), str(row.name), str(row.asset_class))
        for row in configured.itertuples(index=False)
    }
    instrument_lookup = {**company_lookup, **configured_lookup}
    instruments: list[InstrumentSnapshot] = []
    for symbol, group in prices.groupby("symbol", sort=True):
        symbol = str(symbol)
        if symbol not in instrument_lookup:
            continue
        ordered = group.sort_values("trading_date").tail(260).reset_index(drop=True)
        closes = pd.to_numeric(ordered["adjusted_close"], errors="coerce")
        returns = closes.pct_change(fill_method=None)
        last_price = _finite(closes.iloc[-1]) if not closes.empty else None
        daily_return = _finite(returns.iloc[-1]) if len(returns) >= 2 else None
        weekly_return = _finite(closes.iloc[-1] / closes.iloc[-6] - 1) if len(closes) >= 6 else None
        instrument_id, name, asset_class = instrument_lookup[symbol]
        annualization = 365 if asset_class == "crypto" else 252
        volatility = (
            _finite(returns.tail(20).std(ddof=1) * np.sqrt(annualization)) if len(returns.dropna()) >= 20 else None
        )
        trend = "insufficient"
        if len(closes.dropna()) >= 100:
            short = closes.tail(20).mean()
            long = closes.tail(100).mean()
            trend = "uptrend" if short > long else "downtrend"
        history = [
            PricePoint(
                date=_python_date(row.trading_date),
                close=float(row.adjusted_close),
                volume=_finite(row.volume),
            )
            for row in ordered.tail(180).itertuples(index=False)
            if _finite(row.adjusted_close) is not None
        ]
        instruments.append(
            InstrumentSnapshot(
                instrument_id=instrument_id,
                symbol=symbol,
                display_name=name,
                asset_class=asset_class,
                last_price=last_price,
                daily_return=daily_return,
                weekly_return=weekly_return,
                realized_volatility=volatility,
                trend_regime=trend,
                freshness_date=history[-1].date if history else None,
                price_history=history,
            )
        )
    return instruments


def _earnings(database: Database) -> list[EarningsSnapshot]:
    frame = database.frame(
        """
        select f.forecast_id, f.company_id, f.fiscal_quarter, e.earnings_date,
               f.forecast_cutoff_date, f.horizon_days, f.model_name, f.ablation,
               f.forecast_revenue, f.actual_revenue, c.consensus_revenue,
               v.expectation_mode, v.variant, v.variant_zscore, v.confidence_score
        from forecasts f
        join variant_signals v on f.forecast_id = v.forecast_id
        join consensus_estimates c on v.estimate_id = c.estimate_id
        join earnings_calendar e on f.company_id = e.company_id and f.fiscal_quarter = e.fiscal_quarter
        order by f.forecast_cutoff_date desc, abs(v.variant) desc
        limit 1000
        """
    )
    return [
        EarningsSnapshot(
            forecast_id=str(row.forecast_id),
            company_id=str(row.company_id),
            fiscal_quarter=str(row.fiscal_quarter),
            earnings_date=_python_date(row.earnings_date),
            forecast_cutoff_date=_python_date(row.forecast_cutoff_date),
            horizon_days=int(row.horizon_days),
            model_name=str(row.model_name),
            ablation=str(row.ablation),
            forecast_revenue=float(row.forecast_revenue),
            actual_revenue=_finite(row.actual_revenue),
            expectation_revenue=float(row.consensus_revenue),
            expectation_mode=str(row.expectation_mode),
            variant=float(row.variant),
            variant_zscore=_finite(row.variant_zscore),
            confidence_score=_finite(row.confidence_score),
        )
        for row in frame.itertuples(index=False)
    ]


def _equity_signals(database: Database) -> list[ResearchSignalSnapshot]:
    frame = database.frame(
        """
        select v.signal_id, v.company_id, v.forecast_cutoff_date, v.horizon_days,
               v.variant, v.variant_zscore, v.confidence_score, v.expectation_mode,
               f.model_name, f.ablation, e.earnings_date
        from variant_signals v
        join forecasts f on v.forecast_id = f.forecast_id
        join earnings_calendar e on v.company_id = e.company_id and v.fiscal_quarter = e.fiscal_quarter
        order by v.forecast_cutoff_date desc, abs(v.variant_zscore) desc nulls last
        limit 1000
        """
    )
    signals: list[ResearchSignalSnapshot] = []
    for row in frame.itertuples(index=False):
        zscore = _finite(row.variant_zscore)
        posture = "abstain"
        reasons: list[str] = []
        if zscore is not None and zscore >= 0.5:
            posture = "long_research"
        elif zscore is not None and zscore <= -0.5:
            posture = "short_research"
        else:
            reasons.append("Variant magnitude does not clear the research threshold")
        eligibility = "research_only" if str(row.expectation_mode) == "expectation_proxy" else "eligible"
        if eligibility == "research_only":
            reasons.append("Expectation is a seasonal proxy, not historical sell-side consensus")
        signals.append(
            ResearchSignalSnapshot(
                signal_id=str(row.signal_id),
                instrument_id=str(row.company_id),
                asset_class="equity",
                decision_date=_python_date(row.forecast_cutoff_date),
                horizon=f"{int(row.horizon_days)}d pre-event",
                posture=posture,
                eligibility=eligibility,
                strength=zscore,
                confidence_score=_finite(row.confidence_score),
                catalyst=f"SEC filing-date proxy on {_python_date(row.earnings_date).isoformat()}",
                invalidation="Reported revenue and event response do not confirm the model-expectation divergence",
                evidence_summary=f"{row.model_name} · {row.ablation} · {row.expectation_mode}",
                reasons=reasons,
            )
        )
    return signals


def _crypto_signals(database: Database) -> list[ResearchSignalSnapshot]:
    frame = database.frame(
        """
        select signal_id, instrument_id, decision_date, horizon_days, posture,
               direction_probability, expected_return, confidence_score,
               training_samples, calibration_status, explanation
        from market_signals_daily
        where asset_class = 'crypto'
        order by decision_date desc, confidence_score desc
        limit 1000
        """
    )
    signals: list[ResearchSignalSnapshot] = []
    for row in frame.itertuples(index=False):
        reasons: list[str] = []
        if str(row.posture) == "abstain":
            reasons.append("Model probability, expected return, and directional agreement did not all clear gates")
        reasons.append("Signal is research-only and does not estimate realized trading profits")
        signals.append(
            ResearchSignalSnapshot(
                signal_id=str(row.signal_id),
                instrument_id=str(row.instrument_id),
                asset_class="crypto",
                decision_date=_python_date(row.decision_date),
                horizon=f"{int(row.horizon_days)}d close-to-close",
                posture=str(row.posture),
                eligibility="research_only",
                strength=_finite(row.expected_return),
                calibrated_probability=_finite(row.direction_probability),
                confidence_score=_finite(row.confidence_score),
                catalyst="Daily point-in-time trend, momentum, volatility, and volume features",
                invalidation="Direction or expected return no longer clears the fixed evidence gate",
                evidence_summary=(
                    f"Calibrated logistic/HGB ensemble · {int(row.training_samples)} prior samples · "
                    f"{row.calibration_status}"
                ),
                reasons=reasons,
            )
        )
    return signals


def _signals(database: Database) -> list[ResearchSignalSnapshot]:
    return sorted(
        [*_equity_signals(database), *_crypto_signals(database)],
        key=lambda signal: (signal.decision_date, signal.confidence_score or 0),
        reverse=True,
    )


def _model_diagnostics(database: Database) -> list[ModelDiagnosticSnapshot]:
    frame = database.frame(
        """
        select model_name, ablation, horizon_days, forecast_revenue, actual_revenue
        from forecasts where status = 'out_of_sample' and actual_revenue is not null
        """
    )
    diagnostics: list[ModelDiagnosticSnapshot] = []
    for key, group in frame.groupby(["model_name", "ablation", "horizon_days"], dropna=False):
        error = pd.to_numeric(group["forecast_revenue"] - group["actual_revenue"], errors="coerce")
        actual = pd.to_numeric(group["actual_revenue"], errors="coerce").abs().replace(0, np.nan)
        diagnostics.append(
            ModelDiagnosticSnapshot(
                model_name=str(key[0]),
                ablation=str(key[1]),
                horizon_days=int(key[2]),
                observations=len(group),
                mae=float(error.abs().mean()),
                rmse=float(np.sqrt(np.square(error).mean())),
                mape=_finite((error.abs() / actual).mean()),
            )
        )
    return sorted(diagnostics, key=lambda row: (row.horizon_days, row.mae))


def _backtests(database: Database, statistics: dict[str, int | float | None]) -> list[BacktestSnapshot]:
    observations = int(statistics["backtest_observations"] or 0)
    snapshots: list[BacktestSnapshot] = []
    if observations:
        snapshots.append(
            BacktestSnapshot(
                backtest_id="equity_event_variant_demo_v1",
                asset_class="equity",
                strategy_name="Earnings expectation-variant event study",
                readiness="research_only",
                verdict="Exploratory evidence only",
                sample_size=observations,
                development_metrics={"top_bottom_spread_0_3": _finite(statistics["event_spread"])},
                assumptions=[
                    "SEC filing dates proxy for exact earnings timestamps",
                    "Seasonal expectation proxy is not Wall Street consensus",
                    "Daily adjusted closes and market adjustment are used",
                ],
                warnings=[
                    "Small three-company universe",
                    "Repeated model signals reduce the effective event sample",
                    "Borrow, capacity, taxes, and intraday execution are not fully modelled",
                ],
            )
        )
    runs = database.frame(
        """
        select backtest_run_id, asset_class, strategy_name, readiness,
               full_metrics, development_metrics, final_test_metrics,
               protocol, robustness, readiness_reasons
        from backtest_runs order by strategy_name
        """
    )
    for row in runs.itertuples(index=False):
        full_metrics = row.full_metrics if isinstance(row.full_metrics, dict) else {}
        development_metrics = row.development_metrics if isinstance(row.development_metrics, dict) else {}
        final_test_metrics = row.final_test_metrics if isinstance(row.final_test_metrics, dict) else {}
        protocol = row.protocol if isinstance(row.protocol, dict) else {}
        robustness = row.robustness if isinstance(row.robustness, dict) else {}
        warnings = list(row.readiness_reasons) if isinstance(row.readiness_reasons, list) else []
        curve = database.frame(
            """
            select curve_date, net_return, net_equity, drawdown, gross_exposure, turnover
            from backtest_curve
            where backtest_run_id = :run_id order by curve_date
            """,
            {"run_id": str(row.backtest_run_id)},
        )
        returns = pd.to_numeric(curve["net_return"], errors="coerce")
        cadence = 365 / max(int(protocol.get("horizon_days") or 5), 1)
        rolling_mean = returns.rolling(30, min_periods=15).mean()
        rolling_std = returns.rolling(30, min_periods=15).std(ddof=1).replace(0, np.nan)
        curve["rolling_sharpe"] = rolling_mean / rolling_std * np.sqrt(cadence)
        monthly = (
            curve.assign(month=pd.to_datetime(curve["curve_date"]).dt.to_period("M"))
            .groupby("month")["net_return"]
            .apply(lambda values: float((1 + values).prod() - 1))
        )
        sensitivity = database.frame(
            """
            select scenario, parameters, metrics from backtest_sensitivity
            where backtest_run_id = :run_id order by scenario
            """,
            {"run_id": str(row.backtest_run_id)},
        )
        bootstrap = robustness.get("block_bootstrap") if isinstance(robustness.get("block_bootstrap"), dict) else {}
        flattened_robustness = {
            "bootstrap_probability_positive": _finite(bootstrap.get("probability_positive")),
            "bootstrap_ci_low": _finite(bootstrap.get("ci_low")),
            "bootstrap_ci_high": _finite(bootstrap.get("ci_high")),
            "deflated_sharpe_probability": _finite(robustness.get("deflated_sharpe_probability")),
            "profitable_subperiod_fraction": _finite(robustness.get("profitable_subperiod_fraction")),
            "trials_adjusted": _finite(robustness.get("trials_adjusted")),
        }
        snapshots.append(
            BacktestSnapshot(
                backtest_id=str(row.backtest_run_id),
                asset_class=str(row.asset_class),
                strategy_name=str(row.strategy_name),
                readiness=str(row.readiness),
                verdict={
                    "decision_ready": (
                        "Passed the declared statistical research gates; live validation is still required"
                    ),
                    "research_only": "Promising or mixed evidence that does not clear every promotion gate",
                    "not_ready": "Failed one or more hard evidence or risk gates",
                }.get(str(row.readiness), "Unclassified research result"),
                sample_size=int(full_metrics.get("trades") or 0),
                development_metrics={str(key): _finite(value) for key, value in development_metrics.items()},
                final_test_metrics={str(key): _finite(value) for key, value in final_test_metrics.items()},
                full_metrics={str(key): _finite(value) for key, value in full_metrics.items()},
                robustness=flattened_robustness,
                assumptions=[
                    f"One-bar execution lag; {protocol.get('horizon_days', '?')}-day holding horizon",
                    f"Fees {protocol.get('fee_bps', '?')} bps and slippage {protocol.get('slippage_bps', '?')} bps",
                    "15% volatility target with a 100% gross-exposure cap",
                ],
                warnings=[*warnings, "Historical results do not guarantee live profitability"],
                equity_curve=[
                    BacktestPoint(date=_python_date(item.curve_date), value=float(item.net_equity))
                    for item in curve.itertuples(index=False)
                ],
                drawdown_curve=[
                    BacktestPoint(date=_python_date(item.curve_date), value=float(item.drawdown))
                    for item in curve.itertuples(index=False)
                ],
                rolling_sharpe_curve=[
                    BacktestPoint(date=_python_date(item.curve_date), value=float(item.rolling_sharpe))
                    for item in curve.itertuples(index=False)
                    if _finite(item.rolling_sharpe) is not None
                ],
                exposure_curve=[
                    BacktestPoint(date=_python_date(item.curve_date), value=float(item.gross_exposure))
                    for item in curve.itertuples(index=False)
                ],
                turnover_curve=[
                    BacktestPoint(date=_python_date(item.curve_date), value=float(item.turnover))
                    for item in curve.itertuples(index=False)
                ],
                monthly_returns=[
                    BacktestPoint(date=period.to_timestamp(how="end").date(), value=float(value))
                    for period, value in monthly.items()
                ],
                sensitivities=[
                    SensitivitySnapshot(
                        scenario=str(item.scenario),
                        cost_multiplier=float((item.parameters or {}).get("cost_multiplier", 1)),
                        metrics={str(key): _finite(value) for key, value in (item.metrics or {}).items()},
                    )
                    for item in sensitivity.itertuples(index=False)
                ],
            )
        )
    return snapshots


def _quality_issues(database: Database) -> list[QualityIssueSnapshot]:
    frame = database.frame(
        """
        select issue_id, stage, severity, rule, entity_key, message, detected_at
        from data_quality_issues order by detected_at desc limit 500
        """
    )
    return [
        QualityIssueSnapshot(
            issue_id=str(row.issue_id),
            stage=str(row.stage),
            severity=str(row.severity),
            rule=str(row.rule),
            entity_key=str(row.entity_key),
            message=str(row.message),
            detected_at=_python_datetime(row.detected_at) or datetime.now(UTC),
        )
        for row in frame.itertuples(index=False)
    ]


def _pipeline_runs(database: Database) -> list[PipelineRunSnapshot]:
    frame = database.frame(
        """
        select pipeline_run_id, command, mode, started_at, ended_at, status, row_counts, error_summary
        from pipeline_runs order by started_at desc limit 100
        """
    )
    rows: list[PipelineRunSnapshot] = []
    for row in frame.itertuples(index=False):
        counts = row.row_counts if isinstance(row.row_counts, dict) else {}
        rows.append(
            PipelineRunSnapshot(
                pipeline_run_id=str(row.pipeline_run_id),
                command=str(row.command),
                mode=str(row.mode),
                started_at=_python_datetime(row.started_at) or datetime.now(UTC),
                ended_at=_python_datetime(row.ended_at),
                status=str(row.status),
                row_counts={str(key): int(value) for key, value in counts.items()},
                error_summary=str(row.error_summary) if row.error_summary else None,
            )
        )
    return rows


def _finite_metrics(value: Any) -> dict[str, float | None]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _finite(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}


def _causal_audits(database: Database) -> list[CausalAuditSnapshot]:
    frame = database.frame(
        """
        select audit_id, dataset_hash, strategy_id, strategy_version, symbol,
               interval, mode, audited_at, passed, details
        from causal_audits order by audited_at desc, audit_id limit 500
        """
    )
    return [
        CausalAuditSnapshot(
            audit_id=str(row.audit_id),
            dataset_hash=str(row.dataset_hash),
            strategy_id=str(row.strategy_id),
            version=str(row.strategy_version),
            symbol=str(row.symbol),
            interval=str(row.interval),
            mode=str(row.mode),
            audited_at=_python_datetime(row.audited_at) or datetime.now(UTC),
            passed=bool(row.passed),
            outer_block_consumed=bool(
                (row.details if isinstance(row.details, dict) else {}).get("outer_block_consumed", False)
            ),
            details=row.details if isinstance(row.details, dict) else {},
            no_repaint_badge="passed" if bool(row.passed) else "failed",
        )
        for row in frame.itertuples(index=False)
    ]


def _strategies(database: Database, audits: list[CausalAuditSnapshot]) -> list[StrategySnapshot]:
    frame = database.frame(
        """
        select strategy_run_id, dataset_hash, strategy_id, strategy_version, family,
               symbol, interval, mode, run_timestamp, parameters, status, metrics, ended_at
        from strategy_runs order by strategy_id, strategy_version, symbol, interval, mode, run_timestamp
        """
    )
    if frame.empty:
        return []
    for row in frame.itertuples(index=False):
        metrics = row.metrics if isinstance(row.metrics, dict) else {}
        if str(row.mode) == "frozen" and "online_state" in metrics:
            raise ValueError("legacy FROZEN snapshot contains online_state and must be regenerated")
    key_columns = ["dataset_hash", "strategy_id", "strategy_version", "symbol", "interval", "mode"]
    frame["run_timestamp"] = pd.to_datetime(frame["run_timestamp"], utc=True)
    generations = frame.groupby(key_columns, dropna=False).size().to_dict()
    latest = frame.sort_values([*key_columns, "run_timestamp"], kind="stable").drop_duplicates(key_columns, keep="last")
    weights = database.frame(
        """
        select dataset_hash, strategy_id, strategy_version, symbol, interval, mode,
               effective_at, weight
        from ensemble_weights
        order by dataset_hash, strategy_id, strategy_version, symbol, interval, mode, effective_at
        """
    )
    weight_lookup: dict[tuple[str, ...], float] = {}
    for row in weights.itertuples(index=False):
        key = tuple(str(getattr(row, column)) for column in key_columns)
        weight_lookup[key] = float(row.weight)
    audit_lookup: dict[tuple[str, ...], CausalAuditSnapshot] = {}
    for audit in reversed(audits):
        key = (
            audit.dataset_hash,
            audit.strategy_id,
            audit.version,
            audit.symbol,
            audit.interval,
            audit.mode,
        )
        audit_lookup[key] = audit

    snapshots: list[StrategySnapshot] = []
    for row in latest.itertuples(index=False):
        key = tuple(str(getattr(row, column)) for column in key_columns)
        metrics = row.metrics if isinstance(row.metrics, dict) else {}
        promotion = metrics.get("promotion") if isinstance(metrics.get("promotion"), dict) else {}
        audit = audit_lookup.get(key)
        raw_warnings = metrics.get("warnings") if isinstance(metrics.get("warnings"), list) else []
        reasons = promotion.get("reasons") if isinstance(promotion.get("reasons"), list) else []
        warnings = list(
            dict.fromkeys(
                [
                    *(str(item) for item in raw_warnings),
                    *(str(item) for item in reasons),
                    "Historical evidence is not live proof",
                    "Research/paper-trading aid: abstain when uncertainty is material",
                ]
            )
        )
        causal_passed = audit.passed if audit is not None else metrics.get("causal_audit_passed")
        snapshots.append(
            StrategySnapshot(
                strategy_id=str(row.strategy_id),
                version=str(row.strategy_version),
                family=str(row.family),
                symbol=str(row.symbol),
                interval=str(row.interval),
                state=str(metrics.get("state") or row.mode or row.status),
                weight=weight_lookup.get(key, 0.0),
                development_metrics=_finite_metrics(metrics.get("development_metrics")),
                final_test_metrics=_finite_metrics(metrics.get("final_test_metrics")),
                warnings=warnings,
                generation=int(generations[key]),
                progress=1.0 if row.ended_at is not None else 0.5,
                complexity=len(row.parameters) if isinstance(row.parameters, dict) else None,
                promotion_state=(
                    "promoted" if promotion.get("promoted") is True else "rejected" if promotion else str(row.status)
                ),
                causal_audit_passed=bool(causal_passed) if isinstance(causal_passed, bool) else None,
                no_repaint_badge=(
                    "passed" if causal_passed is True else "failed" if causal_passed is False else "not_audited"
                ),
                latest_run_at=_python_datetime(row.run_timestamp),
            )
        )
    return sorted(
        snapshots,
        key=lambda item: (item.strategy_id, item.version, item.symbol, item.interval, item.state),
    )


def _ensemble_components(database: Database) -> list[EnsembleComponentSnapshot]:
    frame = database.frame(
        """
        select dataset_hash, strategy_id, strategy_version, family, symbol, interval,
               mode, effective_at, weight, evidence
        from ensemble_weights
        order by effective_at desc, dataset_hash, strategy_id, strategy_version, symbol, interval, mode
        """
    )
    if frame.empty:
        return []
    key_columns = ["dataset_hash", "strategy_id", "strategy_version", "symbol", "interval", "mode"]
    frame["effective_at"] = pd.to_datetime(frame["effective_at"], utc=True)
    frame["_cohort_id"] = frame["evidence"].map(
        lambda value: str(value.get("cohort_id", "")) if isinstance(value, dict) else ""
    )
    frame = frame.sort_values(
        [
            "effective_at",
            "dataset_hash",
            "_cohort_id",
            "strategy_id",
            "strategy_version",
            "symbol",
            "interval",
            "mode",
        ],
        ascending=[False, True, True, True, True, True, True, True],
        kind="stable",
    )
    cohort_rows = frame[frame["evidence"].map(lambda value: isinstance(value, dict) and bool(value.get("cohort_id")))]
    latest = frame.drop_duplicates(key_columns, keep="first").head(1000)
    if not cohort_rows.empty:
        latest = cohort_rows.iloc[0:0]
        inspected: set[tuple[str, pd.Timestamp]] = set()
        for candidate in cohort_rows.itertuples(index=False):
            evidence = candidate.evidence
            cohort_id = str(evidence["cohort_id"])
            effective_at = pd.Timestamp(candidate.effective_at)
            identity = (cohort_id, effective_at)
            if identity in inspected:
                continue
            inspected.add(identity)
            selected = cohort_rows[
                cohort_rows["evidence"].map(
                    lambda value, selected_cohort=cohort_id: (
                        isinstance(value, dict) and str(value.get("cohort_id")) == selected_cohort
                    )
                )
                & (cohort_rows["effective_at"] == effective_at)
            ]
            members = evidence.get("cohort_members", [])
            expected = {
                (str(item.get("strategy_id")), str(item.get("strategy_version")))
                for item in members
                if isinstance(item, dict)
            }
            observed = {(str(row.strategy_id), str(row.strategy_version)) for row in selected.itertuples(index=False)}
            hashes = {
                row.evidence.get("cohort_decision_hash")
                for row in selected.itertuples(index=False)
                if isinstance(row.evidence, dict)
            }
            if expected and observed == expected and len(selected) == len(expected) and len(hashes) == 1:
                latest = selected.head(1000)
                break
    rows: list[EnsembleComponentSnapshot] = []
    for row in latest.itertuples(index=False):
        evidence = row.evidence if isinstance(row.evidence, dict) else {}
        rows.append(
            EnsembleComponentSnapshot(
                strategy_id=str(row.strategy_id),
                version=str(row.strategy_version),
                family=str(row.family),
                symbol=str(row.symbol),
                interval=str(row.interval),
                mode=str(row.mode),
                effective_at=_python_datetime(row.effective_at) or datetime.now(UTC),
                weight=float(row.weight),
                contribution=_finite(evidence.get("contribution")),
                evidence=evidence,
            )
        )
    return sorted(rows, key=lambda item: (item.strategy_id, item.version, item.symbol, item.interval, item.mode))


def _coverage_gaps(frame: pd.DataFrame, interval: BarInterval) -> list[DatasetGapSnapshot]:
    duration = INTERVAL_DURATION[interval]
    opens = sorted(pd.to_datetime(frame["open_timestamp"], utc=True).drop_duplicates())
    gaps: list[DatasetGapSnapshot] = []
    for previous, current in zip(opens, opens[1:], strict=False):
        difference = current.to_pydatetime() - previous.to_pydatetime()
        missing = max(int(difference / duration) - 1, 0)
        if missing:
            gaps.append(
                DatasetGapSnapshot(
                    start=previous.to_pydatetime() + duration,
                    end=current.to_pydatetime(),
                    missing_bars=missing,
                )
            )
        if len(gaps) == 100:
            break
    return gaps


def _dataset_coverage(database: Database) -> list[DatasetCoverageSnapshot]:
    frame = database.frame(
        """
        select provider, feed, symbol, interval, open_timestamp, close_timestamp,
               available_at, revision, payload_hash
        from market_bars where finalized = true
        order by provider, feed, symbol, interval, open_timestamp, available_at, revision
        """
    )
    latest = frame.drop_duplicates(["provider", "feed", "symbol", "interval", "open_timestamp"], keep="last")
    requests = database.frame(
        """
        select coverage_request_id, provider, feed, symbol, interval, requested_start,
               requested_end, requested_at, status, dataset_hash, row_count, gaps
        from dataset_coverage_requests
        order by provider, feed, symbol, interval, requested_at desc, coverage_request_id desc
        """
    )
    if not requests.empty:
        requests = requests.loc[
            requests.apply(
                lambda row: (
                    isinstance(row["gaps"], dict)
                    and row["gaps"].get("calendar_id")
                    == calendar_for(str(row["provider"]), str(row["feed"])).calendar_id
                    and row["gaps"].get("calendar_version")
                    == calendar_for(str(row["provider"]), str(row["feed"])).version
                ),
                axis=1,
            )
        ]
        if requests.empty:
            return []
        evaluated_hashes = set(
            database.frame("select distinct dataset_hash from strategy_runs where status != 'running'").get(
                "dataset_hash", []
            )
        )
        selected_requests = []
        for _key, group in requests.groupby(["provider", "feed", "symbol", "interval"], sort=True):
            matching = group.loc[group["dataset_hash"].isin(evaluated_hashes)]
            selected_requests.append((matching if not matching.empty else group).iloc[0])
        requests = pd.DataFrame(selected_requests).reset_index(drop=True)
        snapshots: list[DatasetCoverageSnapshot] = []
        for row in requests.itertuples(index=False):
            requested_start = _python_datetime(row.requested_start)
            requested_end = _python_datetime(row.requested_end)
            if requested_start is None or requested_end is None:
                continue
            selected = latest.loc[
                (latest["provider"] == row.provider)
                & (latest["feed"] == row.feed)
                & (latest["symbol"] == row.symbol)
                & (latest["interval"] == row.interval)
                & (pd.to_datetime(latest["open_timestamp"], utc=True) >= requested_start)
                & (pd.to_datetime(latest["open_timestamp"], utc=True) < requested_end)
            ].sort_values("open_timestamp", kind="stable")
            coverage_start = _python_datetime(selected.iloc[0]["open_timestamp"]) if not selected.empty else None
            coverage_end = _python_datetime(selected.iloc[-1]["close_timestamp"]) if not selected.empty else None
            gap_evidence = row.gaps if isinstance(row.gaps, dict) else {}
            missing_gaps = gap_evidence.get("missing", row.gaps if isinstance(row.gaps, list) else [])
            gaps = [
                DatasetGapSnapshot(
                    start=item["start"],
                    end=item["end"],
                    missing_bars=int(item["missing_bars"]),
                )
                for item in missing_gaps[:100]
                if isinstance(item, dict)
            ]
            snapshots.append(
                DatasetCoverageSnapshot(
                    dataset_hash=str(row.dataset_hash),
                    provider=str(row.provider),
                    feed=str(row.feed),
                    symbol=str(row.symbol),
                    interval=str(row.interval),
                    requested_start=requested_start,
                    requested_end=requested_end,
                    coverage_start=coverage_start,
                    coverage_end=coverage_end,
                    row_count=int(row.row_count),
                    gaps=gaps,
                    complete=str(row.status) == "complete" and not gaps,
                    calendar_id=str(gap_evidence.get("calendar_id", "unknown")),
                    calendar_version=str(gap_evidence.get("calendar_version", "unknown")),
                )
            )
        return snapshots[:200]
    if frame.empty:
        return []
    known_hashes = database.frame(
        "select distinct dataset_hash, symbol, interval from strategy_runs order by dataset_hash"
    )
    snapshots: list[DatasetCoverageSnapshot] = []
    for key, group in latest.groupby(["provider", "feed", "symbol", "interval"], sort=True):
        provider, feed, symbol, raw_interval = (str(value) for value in key)
        interval = BarInterval(raw_interval)
        ordered = group.sort_values("open_timestamp", kind="stable").reset_index(drop=True)
        requested_start = _python_datetime(ordered.iloc[0]["open_timestamp"])
        requested_end = _python_datetime(ordered.iloc[-1]["close_timestamp"])
        if requested_start is None or requested_end is None:
            continue
        matches = known_hashes.loc[
            (known_hashes["symbol"] == symbol) & (known_hashes["interval"] == raw_interval), "dataset_hash"
        ].tolist()
        dataset_hash = (
            str(matches[0])
            if len(matches) == 1
            else canonical_hash(
                {
                    "provider": provider,
                    "feed": feed,
                    "symbol": symbol,
                    "interval": raw_interval,
                    "payload_hashes": [str(value) for value in ordered["payload_hash"]],
                }
            )
        )
        gaps = _coverage_gaps(ordered, interval)
        snapshots.append(
            DatasetCoverageSnapshot(
                dataset_hash=dataset_hash,
                provider=provider,
                feed=feed,
                symbol=symbol,
                interval=raw_interval,
                requested_start=requested_start,
                requested_end=requested_end,
                coverage_start=requested_start,
                coverage_end=requested_end,
                row_count=len(ordered),
                gaps=gaps,
                complete=not gaps,
            )
        )
        if len(snapshots) == 200:
            break
    return snapshots


def _rule_complexity(value: Any) -> int:
    if isinstance(value, dict):
        children = value.get("children")
        return 1 + sum(_rule_complexity(child) for child in children) if isinstance(children, list) else 1
    return 0


def _optional_utc(value: Any) -> datetime | None:
    if value is None:
        return None
    try:
        return _python_datetime(value)
    except (TypeError, ValueError):
        return None


def _learning_runs(database: Database, audits: list[CausalAuditSnapshot]) -> list[LearningRunSnapshot]:
    trials_frame = database.frame(
        """
        select trial_id, learning_run_id, candidate_hash, evaluated_at, candidate,
               fitness, status, error_summary
        from learning_trials order by learning_run_id, evaluated_at, trial_id limit 1000
        """
    )
    if trials_frame.empty:
        return []
    rules_frame = database.frame(
        """
        select rule_id, learning_run_id, rule_hash, rule_version, discovered_at,
               state, rule, evidence
        from discovered_rules order by learning_run_id, discovered_at, rule_id limit 500
        """
    )
    audit_by_strategy = {audit.strategy_id: audit for audit in reversed(audits)}
    runs: list[LearningRunSnapshot] = []
    for learning_run_id, trial_group in trials_frame.groupby("learning_run_id", sort=True):
        ordered_trials: list[tuple[int, LearningTrialSnapshot]] = []
        for row in trial_group.itertuples(index=False):
            payload = row.candidate if isinstance(row.candidate, dict) else {}
            rule = payload.get("rule") if isinstance(payload.get("rule"), dict) else {}
            ordinal = int(payload.get("ordinal", len(ordered_trials)))
            ordered_trials.append(
                (
                    ordinal,
                    LearningTrialSnapshot(
                        trial_id=str(row.trial_id),
                        candidate_hash=str(row.candidate_hash),
                        status=str(row.status),
                        fitness=_finite(row.fitness),
                        evaluated_at=_python_datetime(row.evaluated_at) or datetime.now(UTC),
                        rule_text=str(payload.get("rule_text") or "rule unavailable"),
                        complexity=_rule_complexity(rule),
                        error_summary=str(row.error_summary) if row.error_summary else None,
                    ),
                )
            )
        trial_snapshots = [item for _, item in sorted(ordered_trials, key=lambda pair: (pair[0], pair[1].trial_id))][
            -200:
        ]
        discovered: list[DiscoveredRuleSnapshot] = []
        matching_rules = rules_frame.loc[rules_frame["learning_run_id"] == learning_run_id]
        for row in matching_rules.itertuples(index=False):
            rule = row.rule if isinstance(row.rule, dict) else {}
            evidence = row.evidence if isinstance(row.evidence, dict) else {}
            strategy_id = str(rule.get("strategy_id") or f"learned-{str(row.rule_hash)[:16]}")
            audit = audit_by_strategy.get(strategy_id)
            discovered.append(
                DiscoveredRuleSnapshot(
                    rule_id=str(row.rule_id),
                    strategy_id=strategy_id,
                    version=str(row.rule_version),
                    state=str(row.state),
                    rule_text=str(rule.get("plain_language") or "rule unavailable"),
                    fitness=_finite(evidence.get("fitness")),
                    complexity=_rule_complexity(rule.get("canonical")),
                    discovered_at=_python_datetime(row.discovered_at) or datetime.now(UTC),
                    evidence_through=_optional_utc(evidence.get("development_evidence_through")),
                    promotion_state=str(row.state),
                    causal_audit_id=audit.audit_id if audit is not None else None,
                    no_repaint_badge=audit.no_repaint_badge if audit is not None else "not_audited",
                )
            )
        discovered.sort(
            key=lambda item: (
                -float(item.fitness if item.fitness is not None else -math.inf),
                item.rule_id,
            )
        )
        best_rule_detail = discovered[0] if discovered else None
        evidence = (
            matching_rules.iloc[0]["evidence"]
            if not matching_rules.empty and isinstance(matching_rules.iloc[0]["evidence"], dict)
            else {}
        )
        evaluated = len(trial_snapshots)
        budget = max(int(evidence.get("trial_count") or 0), evaluated)
        trial_evidence = trial_group.iloc[0]["candidate"] if not trial_group.empty else {}
        trial_evidence = trial_evidence if isinstance(trial_evidence, dict) else {}
        final_boundary = _optional_utc(evidence.get("final_boundary") or trial_evidence.get("sealed_final_start"))
        if final_boundary is None:
            raise ValueError("learning run is missing its sealed final boundary and must be regenerated")
        audit = audit_by_strategy.get(best_rule_detail.strategy_id) if best_rule_detail is not None else None
        generation = 1
        if "-force-" in str(learning_run_id):
            try:
                generation = int(str(learning_run_id).rsplit("-force-", 1)[1]) + 1
            except ValueError:
                generation = 1
        runs.append(
            LearningRunSnapshot(
                learning_run_id=str(learning_run_id),
                state="completed" if evaluated >= budget else "running",
                evaluated_candidates=evaluated,
                evaluation_budget=budget,
                best_rule=best_rule_detail.rule_text if best_rule_detail is not None else None,
                best_rule_detail=best_rule_detail,
                final_boundary=final_boundary,
                generation=generation,
                progress=float(evaluated / budget) if budget else 0.0,
                trials=trial_snapshots,
                discovered_rules=discovered,
                promotion_state=best_rule_detail.state if best_rule_detail is not None else "no_candidate",
                causal_audit_id=audit.audit_id if audit is not None else None,
                no_repaint_badge=audit.no_repaint_badge if audit is not None else "not_audited",
            )
        )
    return runs


def build_app_snapshot(database: Database, settings: Settings) -> AppSnapshot:
    statistics = research_statistics(database)
    instruments = _instruments(database)
    earnings = _earnings(database)
    signals = _signals(database)
    quality_issues = _quality_issues(database)
    causal_audits = _causal_audits(database)
    overview = OverviewSnapshot(
        company_count=int(statistics["companies"] or 0),
        instrument_count=len(instruments),
        company_quarter_count=int(statistics["company_quarters"] or 0),
        alternative_observation_count=int(statistics["alternative_observations"] or 0),
        forecast_count=int(statistics["historical_forecasts"] or 0),
        signal_count=len(signals),
        event_window_count=int(statistics["backtest_observations"] or 0),
        quality_issue_count=len(quality_issues),
        forecast_mae_improvement=_finite(statistics["forecast_mae_improvement"]),
        alternative_incremental_mae_improvement=_finite(statistics["alternative_incremental_mae_improvement"]),
        event_spread=_finite(statistics["event_spread"]),
    )
    return AppSnapshot(
        metadata=_metadata(database, settings),
        overview=overview,
        instruments=instruments,
        earnings=earnings,
        signals=signals,
        model_diagnostics=_model_diagnostics(database),
        backtests=_backtests(database, statistics),
        quality_issues=quality_issues,
        pipeline_runs=_pipeline_runs(database),
        strategies=_strategies(database, causal_audits),
        ensemble_components=_ensemble_components(database),
        dataset_coverage=_dataset_coverage(database),
        learning_runs=_learning_runs(database, causal_audits),
        causal_audits=causal_audits,
    )
