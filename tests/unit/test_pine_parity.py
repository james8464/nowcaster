from datetime import UTC, datetime

import pytest

from src.intraday.pine_parity import compare_pine_decisions


def _pine(at: str, direction: str) -> dict:
    close = datetime.fromisoformat(at).replace(tzinfo=UTC)
    return {
        "schema_version": 1,
        "source": "tradingview_chart",
        "broker_symbol": "DE30_EUR",
        "strategy_id": "opening_range_15",
        "bar_close_utc_ms": int(close.timestamp() * 1000),
        "direction": direction,
    }


def _journal(at: str, direction: str) -> dict:
    return {
        "kind": "decision",
        "at": at,
        "payload": {
            "broker_symbol": "DE30_EUR",
            "strategy_id": "opening_range_15",
            "status": "no_trade" if direction == "no_trade" else "ready",
            "direction": "" if direction == "no_trade" else direction,
        },
    }


def test_pine_parity_preserves_agreement_disagreement_and_missing_decisions():
    result = compare_pine_decisions(
        [
            _pine("2026-10-14T07:20:00", "long"),
            _pine("2026-10-15T07:20:00", "short"),
            _pine("2026-10-16T07:20:00", "no_trade"),
        ],
        [
            _journal("2026-10-14T07:20:02+00:00", "long"),
            _journal("2026-10-15T07:20:04+00:00", "no_trade"),
            _journal("2026-10-17T07:20:03+00:00", "long"),
        ],
    )
    assert [(row.bar_close_utc, row.status) for row in result.rows] == [
        ("2026-10-14T07:20:00Z", "direction_match"),
        ("2026-10-15T07:20:00Z", "direction_mismatch"),
        ("2026-10-16T07:20:00Z", "missing_nowcaster"),
        ("2026-10-17T07:20:00Z", "missing_pine"),
    ]
    assert result.direction_matches == 1
    assert result.discrepancies == 3
    assert (result.rows[1].pine_direction, result.rows[1].nowcaster_direction) == ("short", "no_trade")


def test_pine_parity_rejects_duplicate_or_misaligned_chart_rows():
    row = _pine("2026-10-14T07:20:00", "long")
    with pytest.raises(ValueError, match="duplicate"):
        compare_pine_decisions([row, row], [])
    with pytest.raises(ValueError, match="opening-range close"):
        compare_pine_decisions([_pine("2026-10-14T07:25:00", "long")], [])


def test_pine_parity_preserves_late_account_abstention_as_feed_evidence():
    result = compare_pine_decisions(
        [_pine("2026-10-14T07:20:00", "no_trade")],
        [_journal("2026-10-14T07:21:16+00:00", "no_trade")],
    )
    assert result.rows[0].status == "late_account_abstention"
    assert result.rows[0].account_delay_ms == 76_000
    assert result.discrepancies == 1


def test_pine_parity_rejects_late_account_entry_as_noncausal():
    with pytest.raises(ValueError, match="late account trade"):
        compare_pine_decisions(
            [_pine("2026-10-14T07:20:00", "long")],
            [_journal("2026-10-14T07:20:16+00:00", "long")],
        )


def test_pine_parity_preserves_mutual_abstention_without_calling_it_a_trade():
    result = compare_pine_decisions(
        [_pine("2026-10-14T07:20:00", "no_trade")],
        [_journal("2026-10-14T07:20:01+00:00", "no_trade")],
    )
    assert result.rows[0].status == "abstention_match"
    assert result.direction_matches == 0
    assert result.discrepancies == 0


def test_pine_parity_rejects_unrepresentable_chart_timestamp():
    row = _pine("2026-10-14T07:20:00", "long")
    row["bar_close_utc_ms"] = 10**30
    with pytest.raises(ValueError, match="timestamp"):
        compare_pine_decisions([row], [])
