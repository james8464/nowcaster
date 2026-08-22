from __future__ import annotations

from datetime import UTC, datetime

from src.app_snapshot.models import AppSnapshot, SnapshotMetadata
from src.app_snapshot.writer import write_snapshot_atomic


def test_snapshot_contract_is_strictly_versioned_and_uses_safe_confidence_copy():
    snapshot = AppSnapshot(
        metadata=SnapshotMetadata(
            generated_at=datetime(2026, 8, 22, tzinfo=UTC),
            git_commit="abc123",
            data_mode="demo_real_snapshot",
            source_posture="Bundled real public snapshots",
            expectation_mode="expectation_proxy",
        )
    )

    assert snapshot.schema_version == 1
    assert "probability of profit" not in snapshot.model_dump_json().lower()


def test_atomic_writer_replaces_a_complete_valid_document(tmp_path):
    snapshot = AppSnapshot(
        metadata=SnapshotMetadata(
            generated_at=datetime(2026, 8, 22, tzinfo=UTC),
            git_commit="abc123",
            data_mode="demo_real_snapshot",
            source_posture="Bundled real public snapshots",
            expectation_mode="expectation_proxy",
        )
    )

    path = write_snapshot_atomic(snapshot, tmp_path / "nested" / "nowcaster-snapshot.json")

    assert AppSnapshot.model_validate_json(path.read_text()).schema_version == 1
    assert not list(path.parent.glob("*.tmp"))
