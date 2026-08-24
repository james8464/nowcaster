from __future__ import annotations

from copy import deepcopy

from src.research.full_history import _semantic_snapshot_payload


class _Snapshot:
    def __init__(self, payload: dict[str, object]):
        self.payload = payload

    def model_dump(self, *, mode: str) -> dict[str, object]:
        assert mode == "json"
        return deepcopy(self.payload)


def test_semantic_snapshot_payload_ignores_export_receipt_metadata() -> None:
    first = _Snapshot(
        {
            "metadata": {
                "generated_at": "2026-08-24T20:00:00Z",
                "last_refresh": "2026-08-24T19:59:00Z",
                "git_commit": "a" * 40,
                "data_mode": "strategy_provider_data",
            },
            "signals": [],
        }
    )
    replay = _Snapshot(
        {
            "metadata": {
                "generated_at": "2026-08-24T21:00:00Z",
                "last_refresh": "2026-08-24T20:59:00Z",
                "git_commit": "b" * 40,
                "data_mode": "strategy_provider_data",
            },
            "signals": [],
        }
    )

    assert _semantic_snapshot_payload(first) == _semantic_snapshot_payload(replay)
