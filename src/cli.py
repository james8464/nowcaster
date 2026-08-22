from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from src.config.settings import Settings
from src.database.engine import Database
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


if __name__ == "__main__":
    app()
