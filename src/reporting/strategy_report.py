from __future__ import annotations

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from src.app_snapshot.models import AppSnapshot


def render_strategy_research_report(snapshot: AppSnapshot) -> str:
    lines = [
        "# Strategy research report",
        "",
        "This artifact is a research/paper-trading aid. Historical evidence is not live proof, "
        "and it does not promise profit.",
        "Treat abstain decisions and uncertainty as first-class outcomes.",
        "",
        f"Snapshot schema: {snapshot.schema_version}",
        f"Generated at: {snapshot.metadata.generated_at.isoformat()}",
        "",
        "## Summary",
        "",
        f"- Backtests: {len(snapshot.backtests)}",
        f"- Signals: {len(snapshot.signals)}",
        f"- Pipeline runs: {len(snapshot.pipeline_runs)}",
        "",
        "The report contains aggregate evidence and provenance only; licensed raw bars are not included.",
        "",
    ]
    strategies = getattr(snapshot, "strategies", [])
    if strategies:
        lines.extend(["## Strategy evidence", ""])
        for strategy in strategies:
            warnings = "; ".join(strategy.warnings) if strategy.warnings else "No additional warnings"
            lines.append(
                f"- {strategy.strategy_id} {strategy.version} · {strategy.symbol} {strategy.interval} · "
                f"state={strategy.state} · warnings={warnings}"
            )
        lines.append("")
    return "\n".join(lines)


def write_strategy_research_report_atomic(snapshot: AppSnapshot, path: Path) -> Path:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = render_strategy_research_report(snapshot)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.stem}-",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return path


__all__ = [
    "render_strategy_research_report",
    "write_strategy_research_report_atomic",
]
