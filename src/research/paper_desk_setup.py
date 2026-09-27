"""Non-destructive first-run setup for the native paper desk, without accounts."""

from datetime import UTC, datetime
from pathlib import Path

from src.research.round_two_contracts import ResearchRoundProtocol
from src.research.round_two_registry import jsonl_writer_lock, load_round_protocol
from src.research.round_two_runtime import register_default_round


def initialize_paper_desk(directory: Path, *, now: datetime | None = None) -> ResearchRoundProtocol:
    directory = Path(directory).expanduser().resolve()
    if "ProspectiveStudies" in directory.parts or "live-paper-study" in directory.parts:
        raise ValueError("protected study cannot be used as a paper desk")
    if directory == directory.parent or directory == Path.home():
        raise ValueError("choose an empty dedicated research directory")
    # Parent-scoped lock serializes simultaneous first-run requests without
    # placing an artifact in the directory whose emptiness we are checking.
    with jsonl_writer_lock(directory.parent / f"{directory.name}-setup"):
        if (directory / "protocol.json").exists():
            return load_round_protocol(directory)
        if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
            raise ValueError("new paper desk requires an empty directory")
        register_default_round(directory, now or datetime.now(UTC))
        return load_round_protocol(directory)
