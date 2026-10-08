from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.intraday.journal import PaperJournal
from src.intraday.report import aggregate_reports, build_report
from src.intraday.session_journal import SessionJournal

T = datetime(2026, 10, 8, 9, tzinfo=UTC)


def append(journal, kind, at, payload):
    with journal as writer:
        writer.append(kind, at, payload)


def test_report_keeps_losses_and_unresolved_positions(tmp_path):
    journal = PaperJournal(tmp_path, "c" * 64)
    append(
        journal,
        "opened",
        T,
        {
            "broker_symbol": "DE30_EUR",
            "strategy_id": "trend_pullback",
            "direction": "long",
            "entry": "100",
            "stop": "95",
            "target": "110",
            "units": "1",
            "notional_gbp": "100",
            "opened_at": T.isoformat(),
        },
    )
    append(
        journal,
        "closed",
        T + timedelta(minutes=5),
        {
            "broker_symbol": "DE30_EUR",
            "direction": "long",
            "gross_pnl_gbp": "-5",
            "commission_gbp": "1",
            "financing_gbp": "1",
            "net_pnl_gbp": "-7",
            "exit_reason": "stop",
        },
    )
    append(
        journal,
        "opened",
        T + timedelta(days=1),
        {
            "broker_symbol": "SPX500_USD",
            "strategy_id": "range_reversion",
            "direction": "short",
            "entry": "5000",
            "stop": "5010",
            "target": "4980",
            "units": "1",
            "notional_gbp": "5000",
            "opened_at": (T + timedelta(days=1)).isoformat(),
        },
    )
    append(
        journal,
        "no_trade",
        T + timedelta(days=1, minutes=1),
        {"broker_symbol": "EUR_USD", "reason": "costs_unverified"},
    )
    report = build_report(journal, round_id="round-2", started_at=T, as_of=T + timedelta(days=2))
    assert report.closed_trades == 1
    assert report.open_positions == 1
    assert report.no_trade_count == 1
    assert report.net_pnl_gbp == Decimal("-7")
    assert report.win_rate == Decimal(0)
    assert report.daily_block_lower_95_gbp is None  # not enough independent days
    assert report.evidence_status == "insufficient_evidence"
    assert report.trades[0].close["net_pnl_gbp"] == "-7"


def test_empty_journal_never_reports_zero_as_proven_expectancy(tmp_path):
    journal = PaperJournal(tmp_path, "c" * 64)
    report = build_report(journal, round_id="round-2", started_at=T, as_of=T)
    assert report.closed_trades == 0
    assert report.win_rate is None
    assert report.net_expectancy_gbp is None
    assert report.profit_factor is None
    assert report.evidence_status == "insufficient_evidence"


def test_report_reconciles_foreign_currency_conversion_charge(tmp_path):
    journal = PaperJournal(tmp_path, "c" * 64)
    append(journal, "opened", T, {
        "broker_symbol": "DE30_EUR", "strategy_id": "trend_pullback", "direction": "long",
        "entry": "100", "opened_at": T.isoformat(), "quote_currency": "EUR",
        "conversion_fee_fraction": "0.01",
    })
    append(journal, "closed", T + timedelta(minutes=5), {
        "broker_symbol": "DE30_EUR", "direction": "long", "gross_pnl_gbp": "10",
        "commission_gbp": "1", "financing_gbp": "1", "conversion_fee_gbp": "0.12",
        "net_pnl_gbp": "7.88", "exit_reason": "target",
    })
    report = build_report(journal, round_id="round-2", started_at=T, as_of=T + timedelta(minutes=5))
    assert report.conversion_fee_gbp == Decimal("0.12")
    assert report.net_pnl_gbp == Decimal("7.88")


def test_high_win_rate_can_still_have_negative_net_result(tmp_path):
    journal = PaperJournal(tmp_path, "c" * 64)
    for index, net in enumerate(("1", "1", "1", "-10")):
        opened = T + timedelta(days=index)
        append(
            journal,
            "opened",
            opened,
            {
                "broker_symbol": "DE30_EUR",
                "strategy_id": "trend_pullback",
                "direction": "long",
                "entry": "100",
                "opened_at": opened.isoformat(),
            },
        )
        append(
            journal,
            "closed",
            opened + timedelta(minutes=5),
            {
                "broker_symbol": "DE30_EUR",
                "direction": "long",
                "gross_pnl_gbp": str(Decimal(net) + 2),
                "commission_gbp": "1",
                "financing_gbp": "1",
                "net_pnl_gbp": net,
                "exit_reason": "target" if Decimal(net) > 0 else "stop",
            },
        )
    report = build_report(journal, round_id="round-2", started_at=T, as_of=T + timedelta(days=4))
    assert report.win_rate == Decimal("0.75")
    assert report.net_pnl_gbp == Decimal("-7")
    assert report.net_expectancy_gbp < 0
    assert report.evidence_status == "insufficient_evidence"


def test_missing_closed_trade_cost_component_is_an_error(tmp_path):
    journal = PaperJournal(tmp_path, "c" * 64)
    append(
        journal,
        "opened",
        T,
        {
            "broker_symbol": "DE30_EUR",
            "strategy_id": "trend_pullback",
            "direction": "long",
            "entry": "100",
            "opened_at": T.isoformat(),
        },
    )
    append(
        journal,
        "closed",
        T + timedelta(minutes=5),
        {"broker_symbol": "DE30_EUR", "gross_pnl_gbp": "5", "net_pnl_gbp": "5"},
    )
    with pytest.raises(ValueError, match="itemized costs"):
        build_report(journal, round_id="round-2", started_at=T, as_of=T + timedelta(hours=1))


def test_live_decisions_and_gaps_are_counted_even_without_paper_entries(tmp_path):
    paper = PaperJournal(tmp_path / "paper", "c" * 64)
    session = SessionJournal(tmp_path / "session", {"round_id": "diagnostic"})
    session.append(
        "decision",
        T,
        {
            "broker_symbol": "DE30_EUR",
            "bar_key": "bar-1",
            "status": "no_trade",
            "reason": "trend_not_confirmed",
            "strategy_id": "trend_pullback",
            "direction": "",
        },
    )
    session.append("gap", T + timedelta(minutes=1), {"reason": "quote_gap_or_reorder", "broker_symbol": "DE30_EUR"})
    report = build_report(
        paper, round_id="diagnostic", started_at=T, as_of=T + timedelta(minutes=2), session_journal=session
    )
    assert report.decisions_count == 1
    assert report.no_trade_count == 1
    assert report.blocked_reasons == {"trend_not_confirmed": 1}
    assert report.feed_gap_count == 1


def test_paper_rejection_of_one_live_setup_is_not_a_second_evaluation(tmp_path):
    paper = PaperJournal(tmp_path / "paper", "c" * 64)
    session = SessionJournal(tmp_path / "session", {"round_id": "diagnostic"})
    session.append(
        "decision",
        T,
        {
            "broker_symbol": "DE30_EUR",
            "bar_key": "bar-1",
            "status": "ready",
            "reason": "breakout",
            "strategy_id": "opening_range_15",
            "direction": "long",
        },
    )
    append(
        paper, "no_trade", T + timedelta(seconds=1), {"broker_symbol": "DE30_EUR", "reason": "cost_evidence_missing"}
    )
    report = build_report(
        paper, round_id="diagnostic", started_at=T, as_of=T + timedelta(minutes=1), session_journal=session
    )
    assert report.decisions_count == 1
    assert report.no_trade_count == 1


def test_as_of_snapshot_can_ignore_later_valid_events_without_erasing_them(tmp_path):
    paper = PaperJournal(tmp_path / "paper", "c" * 64)
    append(paper, "no_trade", T, {"broker_symbol": "DE30_EUR", "reason": "first"})
    append(paper, "no_trade", T + timedelta(minutes=1), {"broker_symbol": "DE30_EUR", "reason": "later"})
    with pytest.raises(ValueError, match="future event"):
        build_report(paper, round_id="day", started_at=T, as_of=T)
    snapshot = build_report(paper, round_id="day", started_at=T, as_of=T, ignore_after_as_of=True)
    assert snapshot.no_trade_count == 1
    assert snapshot.blocked_reasons == {"first": 1}
    later = build_report(paper, round_id="day", started_at=T, as_of=T + timedelta(minutes=1))
    assert later.no_trade_count == 2


def test_aggregate_keeps_prior_day_loss_and_all_stand_aside_decisions(tmp_path):
    reports = []
    for offset, net in ((0, "-7"), (1, "3")):
        opened = T + timedelta(days=offset)
        paper = PaperJournal(tmp_path / str(offset), "c" * 64)
        append(
            paper,
            "opened",
            opened,
            {
                "broker_symbol": "DE30_EUR",
                "strategy_id": "trend_pullback",
                "direction": "long",
                "entry": "100",
                "opened_at": opened.isoformat(),
            },
        )
        append(
            paper,
            "closed",
            opened + timedelta(minutes=5),
            {
                "broker_symbol": "DE30_EUR",
                "direction": "long",
                "gross_pnl_gbp": str(Decimal(net) + 2),
                "commission_gbp": "1",
                "financing_gbp": "1",
                "net_pnl_gbp": net,
            },
        )
        append(
            paper,
            "no_trade",
            opened + timedelta(minutes=6),
            {
                "broker_symbol": "DE30_EUR",
                "reason": "costs_unverified",
            },
        )
        reports.append(
            build_report(paper, round_id=f"day-{offset}", started_at=opened, as_of=opened + timedelta(minutes=10))
        )
    combined = aggregate_reports(reports, as_of=T + timedelta(days=1, minutes=10))
    assert combined.closed_trades == 2
    assert combined.net_pnl_gbp == Decimal("-4")
    assert combined.maximum_drawdown_gbp == Decimal("7")
    assert combined.no_trade_count == 2
    assert combined.blocked_reasons == {"costs_unverified": 2}
    assert len(combined.groups) == 2
    assert combined.evidence_status == "insufficient_evidence"
    with pytest.raises(ValueError, match="duplicate"):
        aggregate_reports([reports[0], reports[0]], as_of=T + timedelta(days=1))
