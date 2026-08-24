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
    if snapshot.deep_research_runs:
        lines.extend(
            [
                "## Deep Research evidence",
                "",
                "Hypothetical research result — every value below is simulated, not a promise or live recommendation.",
                "",
            ]
        )
        for run in snapshot.deep_research_runs:
            budget = "continuous" if run.trial_budget is None else str(run.trial_budget)
            lines.append(
                f"### {run.symbol} {run.interval} · {run.outcome}"
            )
            lines.extend(
                [
                    "",
                    f"- Run: `{run.run_id}`",
                    f"- Provider/feed: {run.provider}/{run.feed}",
                    f"- Attempts: {run.evaluated_attempts}/{budget} "
                    f"({run.succeeded_attempts} succeeded, {run.failed_attempts} failed)",
                    f"- Generation: {run.generation}",
                    f"- Champion score: {run.champion_score if run.champion_score is not None else 'unavailable'}",
                    f"- Final holdout begins: {run.final_test_start.isoformat()}",
                    "- Failed reliability gates:",
                ]
            )
            if run.failed_gates:
                lines.extend(f"  - {gate}" for gate in run.failed_gates)
            else:
                lines.append("  - None recorded")
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
