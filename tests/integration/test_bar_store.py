from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.database.engine import Database
from src.ingestion.bars import BarQuery, MarketBar, atomic_write_bytes
from src.strategies.datasets import BarRepository
from src.strategies.types import BarInterval


def _bar(
    minute: int,
    *,
    available_minute: int,
    close: float,
    payload_hash: str,
    revision: int = 1,
    finalized: bool = True,
) -> MarketBar:
    return MarketBar(
        provider="alpaca",
        feed="iex",
        symbol="AAPL",
        interval=BarInterval.FIVE_MINUTES,
        open_timestamp=datetime(2026, 8, 22, 10, minute, tzinfo=UTC),
        close_timestamp=datetime(2026, 8, 22, 10, minute + 5, tzinfo=UTC),
        available_at=datetime(2026, 8, 22, 10, available_minute, tzinfo=UTC),
        revision=revision,
        finalized=finalized,
        open=100.0,
        high=max(101.0, close),
        low=99.0,
        close=close,
        volume=1_000.0,
        vwap=100.25,
        trade_count=25,
        quote_volume=100_250.0,
        taker_buy_base_volume=400.0,
        taker_buy_quote_volume=40_100.0,
        payload_hash=payload_hash,
    )


def _query() -> BarQuery:
    return BarQuery(
        provider="alpaca",
        feed="iex",
        symbol="AAPL",
        interval=BarInterval.FIVE_MINUTES,
        start=datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
        end=datetime(2026, 8, 22, 10, 15, tzinfo=UTC),
    )


@pytest.fixture
def repository(tmp_path) -> BarRepository:
    database = Database.from_url(f"duckdb:///{tmp_path / 'bars.duckdb'}")
    database.initialize()
    return BarRepository(database)


def test_append_is_idempotent_and_as_of_selects_only_the_revision_available_then(repository):
    original = _bar(0, available_minute=6, close=100.5, payload_hash="a" * 64)
    corrected = _bar(0, available_minute=8, close=100.75, payload_hash="b" * 64)

    assert repository.append([original]) == 1
    assert repository.append([original]) == 0
    assert repository.append([corrected]) == 1

    early = repository.bars_as_of(_query(), datetime(2026, 8, 22, 10, 7, tzinfo=UTC))
    late = repository.bars_as_of(_query(), datetime(2026, 8, 22, 10, 9, tzinfo=UTC))

    assert early[["revision", "close", "payload_hash"]].to_dict("records") == [
        {"revision": 1, "close": 100.5, "payload_hash": "a" * 64}
    ]
    assert late[["revision", "close", "payload_hash"]].to_dict("records") == [
        {"revision": 2, "close": 100.75, "payload_hash": "b" * 64}
    ]
    assert repository.database.scalar("SELECT count(*) FROM market_bars") == 2


def test_append_round_trips_documented_order_flow_fields(repository) -> None:
    repository.append([_bar(0, available_minute=6, close=100.5, payload_hash="a" * 64)])

    stored = repository.bars_as_of(_query(), datetime(2026, 8, 22, 10, 7, tzinfo=UTC)).iloc[0]

    assert stored.quote_volume == 100_250.0
    assert stored.taker_buy_base_volume == 400.0
    assert stored.taker_buy_quote_volume == 40_100.0


def test_adapter_refetch_revision_is_not_visible_before_its_retrieval(repository) -> None:
    original = _bar(0, available_minute=5, close=100.5, payload_hash="a" * 64).model_copy(
        update={"retrieved_at": datetime(2026, 8, 22, 10, 30, tzinfo=UTC)}
    )
    corrected = _bar(0, available_minute=5, close=100.75, payload_hash="b" * 64).model_copy(
        update={"retrieved_at": datetime(2026, 8, 22, 10, 40, tzinfo=UTC)}
    )

    repository.append([original])
    repository.append([corrected])

    before_refetch = repository.bars_as_of(_query(), datetime(2026, 8, 22, 10, 35, tzinfo=UTC))
    after_refetch = repository.bars_as_of(_query(), datetime(2026, 8, 22, 10, 45, tzinfo=UTC))

    assert before_refetch[["revision", "close"]].to_dict("records") == [{"revision": 1, "close": 100.5}]
    assert after_refetch[["revision", "close"]].to_dict("records") == [{"revision": 2, "close": 100.75}]


def test_as_of_uses_source_availability_when_revisions_arrive_out_of_order(repository):
    original = _bar(0, available_minute=6, close=100.5, payload_hash="a" * 64)
    corrected = _bar(0, available_minute=8, close=100.75, payload_hash="b" * 64)

    assert repository.append([corrected]) == 1
    assert repository.append([original]) == 1

    early = repository.bars_as_of(_query(), datetime(2026, 8, 22, 10, 7, tzinfo=UTC))
    late = repository.bars_as_of(_query(), datetime(2026, 8, 22, 10, 9, tzinfo=UTC))

    assert early[["revision", "close", "payload_hash"]].to_dict("records") == [
        {"revision": 2, "close": 100.5, "payload_hash": "a" * 64}
    ]
    assert late[["revision", "close", "payload_hash"]].to_dict("records") == [
        {"revision": 1, "close": 100.75, "payload_hash": "b" * 64}
    ]


def test_manifest_checksum_is_independent_of_revision_ingestion_order(tmp_path):
    original = _bar(0, available_minute=6, close=100.5, payload_hash="a" * 64)
    corrected = _bar(0, available_minute=8, close=100.75, payload_hash="b" * 64)
    chronological_database = Database.from_url(f"duckdb:///{tmp_path / 'chronological.duckdb'}")
    reversed_database = Database.from_url(f"duckdb:///{tmp_path / 'reversed.duckdb'}")
    chronological_database.initialize()
    reversed_database.initialize()
    chronological = BarRepository(chronological_database)
    reversed_order = BarRepository(reversed_database)

    chronological.append([original])
    chronological.append([corrected])
    reversed_order.append([corrected])
    reversed_order.append([original])

    assert chronological.manifest(_query()).dataset_hash == reversed_order.manifest(_query()).dataset_hash


def test_append_rejects_unfinalized_bars(repository):
    incomplete = _bar(0, available_minute=6, close=100.5, payload_hash="a" * 64, finalized=False)

    with pytest.raises(ValueError, match="finalized"):
        repository.append([incomplete])

    assert repository.database.scalar("SELECT count(*) FROM market_bars") == 0


def test_manifest_checksum_is_stable_changes_with_source_and_reports_coverage_gaps(repository):
    first = _bar(0, available_minute=6, close=100.5, payload_hash="a" * 64)
    third = _bar(10, available_minute=16, close=101.5, payload_hash="c" * 64)
    repository.append([first, third])

    initial = repository.manifest(_query())
    assert repository.append([first, third]) == 0
    identical = repository.manifest(_query())

    assert identical.dataset_hash == initial.dataset_hash
    assert initial.row_count == 2
    assert initial.coverage_start == datetime(2026, 8, 22, 10, 0, tzinfo=UTC)
    assert initial.coverage_end == datetime(2026, 8, 22, 10, 15, tzinfo=UTC)
    assert [(gap.start, gap.end, gap.missing_bars) for gap in initial.gaps] == [
        (
            datetime(2026, 8, 22, 10, 5, tzinfo=UTC),
            datetime(2026, 8, 22, 10, 10, tzinfo=UTC),
            1,
        )
    ]

    repository.append([_bar(0, available_minute=7, close=100.75, payload_hash="d" * 64)])
    revised = repository.manifest(_query())

    assert revised.dataset_hash != initial.dataset_hash
    assert revised.row_count == 2


def test_atomic_cache_write_replaces_complete_payload_without_leaving_temporary_file(tmp_path):
    target = tmp_path / "raw" / "page.json"

    atomic_write_bytes(target, b'{"page":1}')
    atomic_write_bytes(target, b'{"page":2}')

    assert target.read_bytes() == b'{"page":2}'
    assert list(target.parent.glob("*.tmp")) == []


def test_strict_revision_as_of_fails_closed_for_rest_backfills(repository) -> None:
    backfilled = _bar(0, available_minute=6, close=100.5, payload_hash="a" * 64).model_copy(
        update={
            "source_available_at": datetime(2026, 8, 22, 10, 5, tzinfo=UTC),
            "observed_at": datetime(2026, 8, 22, 10, 30, tzinfo=UTC),
            "available_at": datetime(2026, 8, 22, 10, 30, tzinfo=UTC),
            "vintage_fidelity": "backfilled_rest_no_revision_history",
        }
    )
    repository.append([backfilled])

    manifest = repository.manifest(_query())

    assert manifest.strict_revision_as_of is False
    assert manifest.vintage_fidelity == "backfilled_rest_no_revision_history"
    with pytest.raises(ValueError, match="revision-as-of.*unavailable"):
        repository.causal_bars_as_of(
            _query(),
            datetime(2026, 8, 22, 10, 35, tzinfo=UTC),
            require_strict_vintage=True,
        )
