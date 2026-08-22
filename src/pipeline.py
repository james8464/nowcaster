from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from src.config.settings import Settings
from src.database.engine import Database
from src.utils.provenance import canonical_hash, git_commit

StageHandler = Callable[[], int | dict[str, int] | None]


@dataclass(frozen=True)
class PipelineSummary:
    completed: tuple[str, ...] = ()
    skipped: tuple[str, ...] = ()
    failed_stage: str | None = None
    error: str = ""
    row_counts: dict[str, int] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.failed_stage is not None

    @property
    def concise_message(self) -> str:
        if self.failed:
            return f"Pipeline failed at {self.failed_stage}: {self.error}"
        return f"Pipeline complete: {len(self.completed)} stages run, {len(self.skipped)} stages reused."


class Pipeline:
    """Ordered, restartable research-stage orchestrator with durable run records."""

    def __init__(
        self,
        settings: Settings,
        *,
        stage_handlers: dict[str, StageHandler],
        stage_order: Sequence[str],
    ):
        self.settings = settings
        self.stage_handlers = stage_handlers
        self.stage_order = tuple(stage_order)
        self.database = Database.from_url(settings.database_url)
        self.database.initialize()

    def _commit(self) -> str:
        try:
            return git_commit(self.settings.project_root)
        except Exception:
            return "unknown"

    def _config_hash(self) -> str:
        return canonical_hash(self.settings.config_hash_payload())

    def _already_complete(self, stage: str, mode: str, config_hash: str) -> bool:
        count = self.database.scalar(
            """
            select count(*) from pipeline_runs
            where command = :command and mode = :mode and config_hash = :config_hash and status = 'success'
            """,
            {"command": stage, "mode": mode, "config_hash": config_hash},
        )
        return bool(count)

    def _record(
        self,
        stage: str,
        mode: str,
        config_hash: str,
        started_at: datetime,
        *,
        status: str,
        row_counts: dict[str, int],
        error: str | None = None,
    ) -> None:
        ended_at = datetime.now(UTC)
        self.database.insert(
            "pipeline_runs",
            [
                {
                    "pipeline_run_id": canonical_hash([stage, mode, config_hash, started_at.isoformat()])[:24],
                    "command": stage,
                    "mode": mode,
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "config_hash": config_hash,
                    "git_commit": self._commit(),
                    "row_counts": row_counts,
                    "status": status,
                    "error_summary": error,
                    "created_at": ended_at,
                }
            ],
        )

    def run(self, stages: Sequence[str], *, mode: str, force: bool = False) -> PipelineSummary:
        requested = set(stages)
        unknown = requested - set(self.stage_order)
        if unknown:
            raise ValueError(f"Unknown pipeline stages: {sorted(unknown)}")
        ordered = [stage for stage in self.stage_order if stage in requested]
        completed: list[str] = []
        skipped: list[str] = []
        totals: dict[str, int] = {}
        config_hash = self._config_hash()
        for stage in ordered:
            if not force and self._already_complete(stage, mode, config_hash):
                skipped.append(stage)
                continue
            started_at = datetime.now(UTC)
            try:
                outcome = self.stage_handlers[stage]()
                if isinstance(outcome, dict):
                    counts = {str(key): int(value) for key, value in outcome.items()}
                else:
                    counts = {stage: int(outcome or 0)}
                totals.update(counts)
                self._record(stage, mode, config_hash, started_at, status="success", row_counts=counts)
                completed.append(stage)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                self._record(stage, mode, config_hash, started_at, status="failed", row_counts={}, error=message)
                return PipelineSummary(tuple(completed), tuple(skipped), stage, message, totals)
        return PipelineSummary(tuple(completed), tuple(skipped), row_counts=totals)
