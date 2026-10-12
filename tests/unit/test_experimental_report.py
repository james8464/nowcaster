"""Experimental P&L is derived from retained paper events, never from a chart."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal as D

import pytest

from src.intraday.contracts import InstrumentSpec
from src.intraday.experimental_report import aggregate_experimental_cohort, build_experimental_report
from src.intraday.experimental_round import ExperimentalRoundManifest
from src.intraday.journal import PaperJournal
from src.intraday.paper import PaperExecutionCosts
from src.intraday.session_journal import SessionJournal
from src.strategies.types import canonical_hash

T = datetime(2026, 10, 20, 7, tzinfo=UTC)


def manifest(day=0):
    start = T + timedelta(days=day)
    return ExperimentalRoundManifest(
        round_id=f"de30-2026-10-{20 + day:02d}-a", cohort_id="de30-or15-long-v1",
        account_feed_hash="a" * 64,
        instrument=InstrumentSpec(provider="oanda_practice", broker_symbol="DE30_EUR", market="germany40",
                                  product="cfd", quote_currency="EUR", point_value=D(1)),
        strategy_id="opening_range_15", direction="long", session_open=start,
        session_close=start + timedelta(hours=1), calendar_hash="c" * 64,
        costs=PaperExecutionCosts(verified=True, slippage_points=D("0.5")), cost_source="official-product-record",
        cost_source_hash="d" * 64, verified_at=T - timedelta(days=1),
        account_currency="GBP", margin_rate=D("0.05"), unit_step=D("0.1"),
        initial_paper_cash=D("10000"), code_hash="b" * 64,
    )


def ledgers(tmp_path, item):
    identity = item.model_dump(mode="json")
    return (PaperJournal(tmp_path / "paper", canonical_hash(identity)),
            SessionJournal(tmp_path / "capture", identity))


def append(paper, kind, at, payload):
    with paper as writer:
        writer.append(kind, at, payload)


def test_zero_trades_is_not_zero_expectancy(tmp_path):
    item = manifest()
    paper, session = ledgers(tmp_path, item)
    report = build_experimental_report(item, paper, session, as_of=T + timedelta(hours=1))
    assert report.qualification == "unqualified"
    assert report.performance.closed_trades == 0
    assert report.performance.net_expectancy_gbp is None
    assert report.performance.win_rate is None
    assert report.expected_eligible_intervals == 12
    assert report.covered_eligible_intervals == 0


def test_wins_losses_fees_and_gaps_all_retained(tmp_path):
    item = manifest()
    paper, session = ledgers(tmp_path, item)
    for index, net in enumerate(("5", "-9")):
        start = T + timedelta(minutes=index * 10)
        append(paper, "opened", start, {"broker_symbol": "DE30_EUR", "strategy_id": "opening_range_15",
                                         "direction": "long", "entry": "100", "opened_at": start.isoformat()})
        append(paper, "closed", start + timedelta(minutes=5),
               {"broker_symbol": "DE30_EUR", "gross_pnl_gbp": str(D(net) + 2),
                "commission_gbp": "1", "financing_gbp": "1", "conversion_fee_gbp": "0",
                "net_pnl_gbp": net})
    session.append("gap", T + timedelta(minutes=30), {"broker_symbol": "DE30_EUR", "reason": "feed_gap"})
    report = build_experimental_report(item, paper, session, as_of=T + timedelta(hours=1))
    assert report.performance.net_pnl_gbp == D("-4")
    assert report.performance.closed_trades == 2
    assert report.performance.feed_gap_count == 1
    assert report.performance.commission_gbp == 2
    assert report.performance.win_rate == D("0.5")


def test_reordered_or_orphan_close_invalidates_report(tmp_path):
    item = manifest()
    paper, session = ledgers(tmp_path, item)
    append(paper, "closed", T + timedelta(minutes=1),
           {"broker_symbol": "DE30_EUR", "gross_pnl_gbp": "1", "commission_gbp": "0",
            "financing_gbp": "0", "net_pnl_gbp": "1"})
    with pytest.raises(ValueError, match="matching open"):
        build_experimental_report(item, paper, session, as_of=T + timedelta(hours=1))


def test_coverage_counts_wholly_missing_intervals(tmp_path):
    item = manifest()
    paper, session = ledgers(tmp_path, item)
    at = T + timedelta(seconds=1)
    session.append("quote", at, {"broker_symbol": "DE30_EUR", "observed_at": at.isoformat(),
                                 "bid": "100", "ask": "102", "tradeable": "True", "source_key": "one"})
    report = build_experimental_report(item, paper, session, as_of=T + timedelta(hours=1))
    assert report.expected_eligible_intervals == 12
    assert report.covered_eligible_intervals == 0  # one tick does not cover a five-minute bar


def test_cohort_aggregate_keeps_losses_and_missing_days(tmp_path):
    reports = []
    for day, net in ((0, "-7"), (2, "3")):
        item = manifest(day)
        paper, session = ledgers(tmp_path / str(day), item)
        append(paper, "opened", item.session_open, {"broker_symbol": "DE30_EUR",
               "strategy_id": "opening_range_15", "direction": "long", "entry": "100",
               "opened_at": item.session_open.isoformat()})
        append(paper, "closed", item.session_open + timedelta(minutes=5),
               {"broker_symbol": "DE30_EUR", "gross_pnl_gbp": str(D(net) + 2),
                "commission_gbp": "1", "financing_gbp": "1", "net_pnl_gbp": net})
        reports.append(build_experimental_report(item, paper, session, as_of=item.session_close))
    result = aggregate_experimental_cohort(tuple(reports), as_of=manifest(2).session_close)
    assert result.performance.net_pnl_gbp == D("-4")
    assert result.performance.closed_trades == 2
    assert result.missing_eligible_days == 1
    assert result.expected_eligible_intervals == 36
    assert result.performance.daily_block_lower_95_gbp is None


def test_old_diagnostic_report_is_unchanged(tmp_path):
    from src.intraday.report import build_report
    old = PaperJournal(tmp_path / "diagnostic", "e" * 64)
    before = build_report(old, round_id="diagnostic", started_at=T, as_of=T)
    assert before.closed_trades == 0
    assert before.net_expectancy_gbp is None
    assert before.rule_identity_hash is None
