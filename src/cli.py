from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from src.config.settings import Settings
from src.database.engine import Database
from src.demo import DEMO_STAGES, demo_pipeline, live_pipeline, run_demo
from src.reporting.recruiter import generate_resume_bullets
from src.reporting.research_report import generate_research_report
from src.utils.logging import configure_logging

app = typer.Typer(help="Alternative-data earnings nowcasting research pipeline.", no_args_is_help=True)
DEFAULT_PROJECT_ROOT = Path.cwd()


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
    force: Annotated[bool, typer.Option(help="Re-run completed stages.")] = False,
) -> None:
    """Build the complete keyless demo from bundled real public snapshots."""
    settings = _load_settings(project_root, database_url, "demo")
    summary = run_demo(settings, force=force)
    if summary.failed:
        typer.echo(summary.concise_message, err=True)
        raise typer.Exit(code=1)
    database = Database.from_url(settings.database_url)
    generate_research_report(database, settings.project_root / "reports" / "latest_research_report.md")
    generate_resume_bullets(database, settings.project_root / "reports" / "resume_bullets.md")
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
    """Generate the measured research note and resume bullets."""
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
    bullet_path = generate_resume_bullets(database, destination / "resume_bullets.md")
    typer.echo(f"Generated {report_path} and {bullet_path}")


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
