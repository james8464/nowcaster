from __future__ import annotations

import json
import os
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import typer

from src.app_snapshot import build_app_snapshot, write_snapshot_atomic
from src.config.settings import Settings
from src.database.engine import Database
from src.demo import DEMO_STAGES, demo_pipeline, live_pipeline, run_demo
from src.reporting.research_report import generate_research_report
from src.research import run_full_strategy_research
from src.strategies.pipeline import (
    BarProviderName,
    DeepResearchOptions,
    EvaluationOptions,
    ExportOptions,
    IngestOptions,
    LearningOptions,
    PipelineEvent,
    StageOutcome,
    StrategyScope,
    create_strategy_pipeline,
)
from src.strategies.types import BarInterval, StrategyMode
from src.trading.service import (
    create_paper_supervisor,
    create_shadow_supervisor,
    paper_credentials_from_environment,
    trading_status,
)
from src.utils.logging import configure_logging

app = typer.Typer(help="Alternative-data earnings nowcasting research pipeline.", no_args_is_help=True)
strategy_app = typer.Typer(help="Run scoped intraday strategy research stages.", no_args_is_help=True)
trading_app = typer.Typer(help="Operate fail-closed shadow and Alpaca paper sessions.", no_args_is_help=True)
app.add_typer(strategy_app, name="strategy")
app.add_typer(trading_app, name="trading")
DEFAULT_PROJECT_ROOT = Path.cwd()


def _trading_settings(project_root: Path, database_url: str | None) -> Settings:
    settings = Settings.load(project_root, mode="live")
    if database_url:
        settings = settings.model_copy(update={"database_url": database_url})
    return settings


@trading_app.command("paper")
def trading_paper(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Authenticate to the fixed Alpaca paper endpoint and reconcile once."""
    try:
        paper_credentials_from_environment()
    except ValueError:
        typer.echo(json.dumps({"event": "paper_error", "reason": "paper_credentials_required"}), err=True)
        raise typer.Exit(code=1) from None
    try:
        settings = _trading_settings(project_root, database_url)
        session_id = f"paper-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
        supervisor = create_paper_supervisor(settings, session_id=session_id)
        result = supervisor.start()
        typer.echo(json.dumps({"event": "paper_checked", "status": result.status, "live": "locked"}, sort_keys=True))
        supervisor.shutdown("check_complete")
        if result.status != "matched":
            raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception as error:
        typer.echo(json.dumps({"event": "paper_error", "error": type(error).__name__}), err=True)
        raise typer.Exit(code=1) from None


@trading_app.command("shadow")
def trading_shadow(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Run a local broker-shaped reconciliation with no external effects."""
    settings = _trading_settings(project_root, database_url)
    session_id = f"shadow-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')}"
    supervisor = create_shadow_supervisor(settings, session_id=session_id)
    result = supervisor.start()
    supervisor.shutdown("check_complete")
    typer.echo(json.dumps({"event": "shadow_checked", "status": result.status, "live": "locked"}, sort_keys=True))


@trading_app.command("status")
def trading_status_command(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Print a bounded, secret-free summary of the latest trading session."""
    typer.echo(json.dumps(trading_status(_trading_settings(project_root, database_url)), sort_keys=True, default=str))


@trading_app.command("reconcile")
def trading_reconcile() -> None:
    """Explain how reconciliation is requested from the owning session."""
    typer.echo(json.dumps({"event": "reconcile_unavailable", "reason": "no_active_session_control_channel"}))
    raise typer.Exit(code=1)


@trading_app.command("freeze")
def trading_freeze() -> None:
    """Fail closed when no active supervised session control channel exists."""
    typer.echo(json.dumps({"event": "freeze_unavailable", "reason": "no_active_session_control_channel"}))
    raise typer.Exit(code=1)


@trading_app.command("stop")
def trading_stop() -> None:
    """Fail closed when no active supervised session control channel exists."""
    typer.echo(json.dumps({"event": "stop_unavailable", "reason": "no_active_session_control_channel"}))
    raise typer.Exit(code=1)


@trading_app.command("flatten")
def trading_flatten(
    account_suffix: Annotated[str, typer.Option(prompt=True, hide_input=False)],
    confirmation: Annotated[str, typer.Option(prompt=True, hide_input=False)],
) -> None:
    """Validate destructive confirmation; the owning supervised session must execute it."""
    if confirmation != f"FLATTEN {account_suffix}":
        typer.echo(json.dumps({"event": "flatten_rejected", "reason": "exact_confirmation_required"}))
        raise typer.Exit(code=1)
    typer.echo(json.dumps({"event": "flatten_unavailable", "reason": "no_active_session_control_channel"}))
    raise typer.Exit(code=1)


@app.callback()
def main() -> None:
    """Run reproducible earnings-nowcasting research stages."""


@app.command("init-db")
def init_db(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Create all normalized research tables."""
    settings = Settings.load(project_root)
    configure_logging(settings.project_root / "logs", settings.log_level)
    database = Database.from_url(database_url or settings.database_url)
    database.initialize()
    typer.echo(f"Initialized {len(database.table_names())} tables at {database_url or settings.database_url}")


def _load_settings(project_root: Path, database_url: str | None, mode: str) -> Settings:
    settings = Settings.load(project_root, mode=mode)
    if database_url:
        settings = settings.model_copy(update={"database_url": database_url})
    configure_logging(settings.project_root / "logs", settings.log_level)
    return settings


def _run_stages(project_root: Path, database_url: str | None, mode: str, stages: tuple[str, ...], force: bool) -> None:
    settings = _load_settings(project_root, database_url, mode)
    if mode not in {"demo", "live"}:
        raise typer.BadParameter("Mode must be 'demo' or 'live'")
    pipeline = demo_pipeline(settings) if mode == "demo" else live_pipeline(settings)
    summary = pipeline.run(stages, mode=mode, force=force)
    typer.echo(summary.concise_message)
    if summary.failed:
        raise typer.Exit(code=1)


@app.command("demo")
def demo(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
    mode: Annotated[str, typer.Option(help="Native-app compatibility mode; must remain demo.")] = "demo",
    force: Annotated[bool, typer.Option(help="Re-run completed stages.")] = False,
) -> None:
    """Build the complete keyless demo from bundled real public snapshots."""
    if mode != "demo":
        raise typer.BadParameter("The keyless demo command supports only --mode demo")
    settings = _load_settings(project_root, database_url, mode)
    summary = run_demo(settings, force=force)
    if summary.failed:
        typer.echo(summary.concise_message, err=True)
        raise typer.Exit(code=1)
    database = Database.from_url(settings.database_url)
    generate_research_report(database, settings.project_root / "reports" / "latest_research_report.md")
    typer.echo(f"Demo complete. {summary.concise_message}")


@app.command("fetch-fundamentals")
def fetch_fundamentals(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
    mode: Annotated[str, typer.Option()] = "demo",
    force: Annotated[bool, typer.Option()] = False,
) -> None:
    """Ingest companies, SEC quarterly facts, and event dates."""
    _run_stages(project_root, database_url, mode, ("ingest_fundamentals",), force)


@app.command("fetch-prices")
def fetch_prices(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
    mode: Annotated[str, typer.Option()] = "demo",
    force: Annotated[bool, typer.Option()] = False,
) -> None:
    """Ingest adjusted daily company, market, and sector prices."""
    _run_stages(project_root, database_url, mode, ("ingest_prices",), force)


@app.command("fetch-altdata")
def fetch_altdata(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
    mode: Annotated[str, typer.Option()] = "demo",
    force: Annotated[bool, typer.Option()] = False,
) -> None:
    """Ingest point-in-time public attention data."""
    _run_stages(project_root, database_url, mode, ("ingest_alternative",), force)


@app.command("build-features")
def build_features(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
    mode: Annotated[str, typer.Option()] = "demo",
    force: Annotated[bool, typer.Option()] = False,
) -> None:
    """Build leakage-audited features at pre-earnings cutoffs."""
    _run_stages(project_root, database_url, mode, ("build_features",), force)


@app.command("train")
def train(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
    mode: Annotated[str, typer.Option()] = "demo",
    force: Annotated[bool, typer.Option()] = False,
) -> None:
    """Generate expanding-window out-of-sample forecasts."""
    _run_stages(project_root, database_url, mode, ("train",), force)


@app.command("backtest")
def backtest(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
    mode: Annotated[str, typer.Option()] = "demo",
    force: Annotated[bool, typer.Option()] = False,
) -> None:
    """Construct expectation variants and evaluate earnings-event returns."""
    _run_stages(project_root, database_url, mode, ("variant", "backtest"), force)


@app.command("report")
def report(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
    mode: Annotated[str, typer.Option()] = "demo",
    output_dir: Annotated[Path | None, typer.Option(file_okay=False)] = None,
) -> None:
    """Generate the measured research note."""
    settings = _load_settings(project_root, database_url, mode)
    database = Database.from_url(settings.database_url)
    database.initialize()
    if mode == "demo" and not database.scalar("select count(*) from forecasts"):
        summary = run_demo(settings)
        if summary.failed:
            typer.echo(summary.concise_message, err=True)
            raise typer.Exit(code=1)
    destination = output_dir or settings.project_root / "reports"
    report_path = generate_research_report(database, destination / "latest_research_report.md")
    typer.echo(f"Generated {report_path}")


@app.command("export-app-snapshot")
def export_app_snapshot(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
    mode: Annotated[str, typer.Option()] = "demo",
    output: Annotated[Path | None, typer.Option(dir_okay=False)] = None,
) -> None:
    """Export a validated, versioned snapshot for the native macOS app."""
    settings = _load_settings(project_root, database_url, mode)
    database = Database.from_url(settings.database_url)
    database.initialize()
    destination = output or settings.project_root / "data" / "app" / "nowcaster-snapshot.json"
    if not destination.is_absolute():
        destination = settings.project_root / destination
    snapshot = build_app_snapshot(database, settings)
    path = write_snapshot_atomic(snapshot, destination)
    schema_version = snapshot.schema_version
    typer.echo(json.dumps({"event": "snapshot_exported", "path": str(path), "schema_version": schema_version}))


def _strategy_scope(
    strategy_id: list[str] | None,
    provider: str,
    feed: str,
    symbol: str,
    interval: str,
    mode: str,
) -> StrategyScope:
    return StrategyScope(
        strategy_ids=strategy_id or (),
        provider=provider,
        feed=feed,
        symbol=symbol,
        interval=interval,
        mode=mode,
    )


def _strategy_pipeline(
    project_root: Path,
    database_url: str | None,
    csv_path: Path | None,
):
    settings = _load_settings(project_root, database_url, "demo")
    database = Database.from_url(settings.database_url)
    return create_strategy_pipeline(settings, database, csv_path=csv_path)


def _emit_strategy_event(event: PipelineEvent) -> None:
    typer.echo(event.json_line())


def _run_strategy_stage(stage: str, operation: Callable[[Callable[[PipelineEvent], None]], StageOutcome]) -> None:
    _emit_strategy_event(PipelineEvent(event="started", stage=stage, progress=0, message=f"{stage} started"))
    try:
        outcome = operation(_emit_strategy_event)
    except Exception as error:
        _emit_strategy_event(
            PipelineEvent(event="error", stage=stage, progress=1, message=f"{type(error).__name__}: {error}")
        )
        raise typer.Exit(code=1) from error
    if outcome.status == "unavailable":
        _emit_strategy_event(PipelineEvent(event="error", stage=stage, progress=1, message=outcome.message))
        raise typer.Exit(code=1)
    _emit_strategy_event(PipelineEvent(event="complete", stage=stage, progress=1, message=outcome.message))


@strategy_app.command("ingest")
def strategy_ingest(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
    strategy_id: Annotated[list[str] | None, typer.Option()] = None,
    provider: Annotated[str, typer.Option()] = BarProviderName.BINANCE.value,
    feed: Annotated[str, typer.Option()] = "spot",
    symbol: Annotated[str, typer.Option()] = "BTCUSDT",
    interval: Annotated[str, typer.Option()] = BarInterval.FIVE_MINUTES.value,
    mode: Annotated[str, typer.Option()] = StrategyMode.PAPER.value,
    start: Annotated[str, typer.Option()] = "2026-01-01T00:00:00Z",
    end: Annotated[str, typer.Option()] = "2026-01-02T00:00:00Z",
    csv_path: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    force: Annotated[bool, typer.Option(help="Refresh only this exact scoped range.")] = False,
) -> None:
    """Append finalized bar revisions for missing scoped coverage."""

    def execute(emit: Callable[[PipelineEvent], None]) -> StageOutcome:
        pipeline = _strategy_pipeline(project_root, database_url, csv_path)
        options = IngestOptions(
            scope=_strategy_scope(strategy_id, provider, feed, symbol, interval, mode),
            start=start,
            end=end,
            force=force,
        )
        return pipeline.ingest(options, emit)

    _run_strategy_stage("ingest", execute)


@strategy_app.command("evaluate")
def strategy_evaluate(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
    strategy_id: Annotated[list[str] | None, typer.Option()] = None,
    provider: Annotated[str, typer.Option()] = BarProviderName.BINANCE.value,
    feed: Annotated[str, typer.Option()] = "spot",
    symbol: Annotated[str, typer.Option()] = "BTCUSDT",
    interval: Annotated[str, typer.Option()] = BarInterval.FIVE_MINUTES.value,
    mode: Annotated[str, typer.Option()] = StrategyMode.PAPER.value,
    csv_path: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    force: Annotated[bool, typer.Option(help="Recompute only the selected immutable cache key.")] = False,
) -> None:
    """Evaluate all compatible local history with sealed causal evidence."""

    def execute(emit: Callable[[PipelineEvent], None]) -> StageOutcome:
        pipeline = _strategy_pipeline(project_root, database_url, csv_path)
        options = EvaluationOptions(
            scope=_strategy_scope(strategy_id, provider, feed, symbol, interval, mode),
            force=force,
        )
        return pipeline.evaluate(options, emit)

    _run_strategy_stage("evaluate", execute)


@strategy_app.command("learn")
def strategy_learn(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
    strategy_id: Annotated[list[str] | None, typer.Option()] = None,
    provider: Annotated[str, typer.Option()] = BarProviderName.BINANCE.value,
    feed: Annotated[str, typer.Option()] = "spot",
    symbol: Annotated[str, typer.Option()] = "BTCUSDT",
    interval: Annotated[str, typer.Option()] = BarInterval.FIVE_MINUTES.value,
    mode: Annotated[str, typer.Option()] = StrategyMode.WALK_FORWARD_LEARNING.value,
    csv_path: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    evaluation_budget: Annotated[int, typer.Option()] = 20,
    seed: Annotated[int, typer.Option()] = 42,
    force: Annotated[bool, typer.Option(help="Append a new run for only the selected learning key.")] = False,
) -> None:
    """Run bounded interpretable rule discovery inside the development boundary."""

    def execute(emit: Callable[[PipelineEvent], None]) -> StageOutcome:
        pipeline = _strategy_pipeline(project_root, database_url, csv_path)
        options = LearningOptions(
            scope=_strategy_scope(strategy_id, provider, feed, symbol, interval, mode),
            evaluation_budget=evaluation_budget,
            seed=seed,
            force=force,
        )
        return pipeline.learn(options, emit)

    _run_strategy_stage("learn", execute)


@strategy_app.command("deep-research")
def strategy_deep_research(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
    strategy_id: Annotated[list[str] | None, typer.Option()] = None,
    provider: Annotated[str, typer.Option()] = BarProviderName.BINANCE.value,
    feed: Annotated[str, typer.Option()] = "spot",
    symbol: Annotated[str, typer.Option()] = "BTCUSDT",
    interval: Annotated[str, typer.Option()] = BarInterval.FIVE_MINUTES.value,
    csv_path: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    workers: Annotated[int, typer.Option(min=1)] = max(1, os.cpu_count() or 1),
    evaluation_budget: Annotated[int | None, typer.Option(min=1, max=100_000)] = None,
    continuous: Annotated[bool, typer.Option()] = False,
    time_budget_seconds: Annotated[int | None, typer.Option(min=1)] = None,
    seed: Annotated[int, typer.Option()] = 42,
    control_directory: Annotated[Path | None, typer.Option(file_okay=False)] = None,
    control_nonce: Annotated[str | None, typer.Option(hidden=True)] = None,
    run_id: Annotated[str | None, typer.Option()] = None,
    resume_run_id: Annotated[str | None, typer.Option()] = None,
) -> None:
    """Search local authenticated history for robust research-only challengers."""

    def execute(emit: Callable[[PipelineEvent], None]) -> StageOutcome:
        pipeline = _strategy_pipeline(project_root, database_url, csv_path)
        selected_control_directory = control_directory or project_root / "data" / "deep-research-control"
        options = DeepResearchOptions(
            scope=_strategy_scope(
                strategy_id,
                provider,
                feed,
                symbol,
                interval,
                StrategyMode.WALK_FORWARD_LEARNING.value,
            ),
            workers=workers,
            evaluation_budget=None if continuous else evaluation_budget or 100,
            continuous=continuous,
            time_budget_seconds=time_budget_seconds,
            seed=seed,
            control_directory=selected_control_directory,
            control_nonce=control_nonce or secrets.token_hex(32),
            run_id=run_id,
            resume_run_id=resume_run_id,
        )
        return pipeline.deep_research(options, emit)

    _run_strategy_stage("deep_research", execute)


@strategy_app.command("export")
def strategy_export(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
    output: Annotated[Path | None, typer.Option(dir_okay=False)] = None,
    report_output: Annotated[Path | None, typer.Option(dir_okay=False)] = None,
) -> None:
    """Export the native snapshot and a bounded aggregate strategy report."""
    snapshot_path = output or project_root / "data" / "app" / "nowcaster-snapshot.json"
    report_path = report_output or project_root / "reports" / "latest_strategy_report.md"

    def execute(emit: Callable[[PipelineEvent], None]) -> StageOutcome:
        pipeline = _strategy_pipeline(project_root, database_url, None)
        return pipeline.export(ExportOptions(snapshot_path=snapshot_path, report_path=report_path), emit)

    _run_strategy_stage("export", execute)


@strategy_app.command("research")
def strategy_research(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
    profile: Annotated[str, typer.Option(help="Network-free 'ci' or provider-backed 'live'.")] = "ci",
    output_dir: Annotated[Path, typer.Option(file_okay=False)] = Path("reports/strategy-research"),
    cache_dir: Annotated[Path | None, typer.Option(file_okay=False)] = None,
    cutoff: Annotated[str | None, typer.Option(help="Fixed UTC live cutoff, for example 2026-08-24T00:00:00Z.")] = None,
    max_chunks_per_scope: Annotated[
        int | None,
        typer.Option(help="Diagnostic live limit; incomplete history remains unavailable."),
    ] = None,
) -> None:
    """Publish reproducible full-history manifests and compact strategy research."""
    if profile not in {"ci", "live"}:
        raise typer.BadParameter("profile must be 'ci' or 'live'")
    destination = output_dir if output_dir.is_absolute() else project_root / output_dir
    selected_database_url = database_url or f"duckdb:///{(destination / 'research.duckdb').resolve()}"
    settings = _load_settings(project_root, selected_database_url, "test" if profile == "ci" else "live")
    selected_cutoff = None
    if cutoff is not None:
        try:
            selected_cutoff = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
        except ValueError as error:
            raise typer.BadParameter("cutoff must be an ISO-8601 UTC timestamp") from error
        if selected_cutoff.tzinfo is None or selected_cutoff.utcoffset() != timedelta(0):
            raise typer.BadParameter("cutoff must be an explicit UTC timestamp")
        selected_cutoff = selected_cutoff.astimezone(UTC).replace(tzinfo=UTC)
    summary = run_full_strategy_research(
        settings,
        database_url=selected_database_url,
        output_dir=destination,
        profile=profile,
        cache_dir=cache_dir,
        cutoff=selected_cutoff,
        max_chunks_per_scope=max_chunks_per_scope,
    )
    typer.echo(
        json.dumps(
            {
                "event": "strategy_research_complete",
                "profile": profile,
                "status": summary.get("attempt_status", "completed"),
                "output_dir": str(destination.resolve()),
            },
            sort_keys=True,
        )
    )


@app.command("run-all")
def run_all(
    project_root: Annotated[Path, typer.Option(exists=True, file_okay=False)] = DEFAULT_PROJECT_ROOT,
    database_url: Annotated[str | None, typer.Option()] = None,
    mode: Annotated[str, typer.Option()] = "demo",
    force: Annotated[bool, typer.Option()] = False,
) -> None:
    """Run every ingestion, feature, forecast, signal, and backtest stage."""
    _run_stages(project_root, database_url, mode, DEMO_STAGES, force)


if __name__ == "__main__":
    app()
