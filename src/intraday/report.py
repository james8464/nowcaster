"""Rebuildable, all-decision paper result from the immutable journal."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.intraday.journal import PaperJournal
from src.intraday.live_service import LiveRoundManifest
from src.intraday.research import _daily_block_lower
from src.intraday.session_journal import SessionJournal
from src.strategies.types import canonical_hash

D = Decimal


class TradeRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    open: dict[str, str]
    close: dict[str, str]


class ReportGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    broker_symbol: str
    strategy_id: str
    direction: str
    session_date: str
    closed_trades: int
    net_pnl_gbp: Decimal
    win_rate: Decimal | None


class PaperDecisionReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    schema_version: Literal[1] = 1
    paper_only: Literal[True] = True
    round_id: str
    started_at: datetime
    generated_at: datetime
    declared_session_end_at: datetime | None = None
    price_scope: Literal["account_practice_paper"] = "account_practice_paper"
    evidence_status: Literal["insufficient_evidence"] = "insufficient_evidence"
    rule_identity_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    coverage_status: Literal["not_measured", "measured"] = "not_measured"
    account_quote_coverage: Decimal | None = None
    expected_quote_intervals: int | None = Field(default=None, ge=0)
    covered_quote_intervals: int | None = Field(default=None, ge=0)
    decisions_count: int
    no_trade_count: int
    blocked_reasons: dict[str, int]
    feed_gap_count: int
    eligible_session_gap_count: int | None = Field(default=None, ge=0)
    closed_trades: int
    open_positions: int
    gross_pnl_gbp: Decimal
    commission_gbp: Decimal
    financing_gbp: Decimal
    conversion_fee_gbp: Decimal = D(0)
    net_pnl_gbp: Decimal
    win_rate: Decimal | None
    net_expectancy_gbp: Decimal | None
    profit_factor: Decimal | None
    maximum_drawdown_gbp: Decimal
    daily_block_lower_95_gbp: Decimal | None
    groups: tuple[ReportGroup, ...]
    trades: tuple[TradeRecord, ...]
    unresolved_positions: tuple[dict[str, str], ...]
    warning: str = "Paper results use hypothetical fills. Feed coverage and real-money execution are not verified."

    @model_validator(mode="after")
    def consistent(self):
        if self.started_at.tzinfo is None or self.started_at.utcoffset() != timedelta(0):
            raise ValueError("report start must be UTC")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() != timedelta(0):
            raise ValueError("report time must be UTC")
        if self.generated_at < self.started_at or self.closed_trades != len(self.trades):
            raise ValueError("report chronology or trade count invalid")
        if self.declared_session_end_at is not None and (
            self.declared_session_end_at.tzinfo is None
            or self.declared_session_end_at.utcoffset() != timedelta(0)
            or self.declared_session_end_at <= self.started_at
        ):
            raise ValueError("declared session end must be UTC and after report start")
        if self.open_positions != len(self.unresolved_positions):
            raise ValueError("unresolved position count invalid")
        if self.net_pnl_gbp != self.gross_pnl_gbp - self.commission_gbp - self.financing_gbp - self.conversion_fee_gbp:
            raise ValueError("paper P&L components do not reconcile")
        if (self.coverage_status == "measured") != (self.account_quote_coverage is not None):
            raise ValueError("coverage status and value disagree")
        if (self.expected_quote_intervals is None) != (self.covered_quote_intervals is None):
            raise ValueError("coverage interval counts must be paired")
        if self.expected_quote_intervals is not None:
            if self.expected_quote_intervals <= 0 or self.covered_quote_intervals > self.expected_quote_intervals:
                raise ValueError("coverage interval counts invalid")
            if self.account_quote_coverage != D(self.covered_quote_intervals) / D(self.expected_quote_intervals):
                raise ValueError("coverage fraction disagrees with intervals")
        return self


def build_report(
    journal: PaperJournal,
    manifest: LiveRoundManifest | None = None,
    *,
    round_id: str | None = None,
    started_at: datetime | None = None,
    as_of: datetime,
    session_journal: SessionJournal | None = None,
    ignore_after_as_of: bool = False,
) -> PaperDecisionReport:
    if manifest is not None:
        if journal.protocol_hash != manifest.identity_hash:
            raise ValueError("paper journal does not match live round")
        round_id = manifest.round_id
        # A monitor may start before the first declared session. Its empty
        # pre-session report is still current, not a future-dated result.
        started_at = min(as_of, min(window.opened_at for window in manifest.sessions.values()))
    if started_at is None or round_id is None:
        raise ValueError("report needs frozen round identity")
    if not round_id.strip() or as_of < started_at:
        raise ValueError("invalid round or report window")
    opens: dict[str, dict[str, str]] = {}
    trades: list[TradeRecord] = []
    reasons: Counter[str] = Counter()
    daily: dict[str, Decimal] = defaultdict(lambda: D(0))
    equity_delta = D(0)
    peak_delta = D(0)
    maximum_drawdown = D(0)
    gaps = 0
    eligible_gaps: int | None = 0 if manifest is not None and session_journal is not None else None
    decisions = 0
    session_events = session_journal.events() if session_journal is not None else ()
    if session_journal is not None:
        for event in session_events:
            if datetime.fromisoformat(event["at"]) > as_of:
                if ignore_after_as_of:
                    continue
                raise ValueError("session journal contains future event relative to report")
            if event["kind"] == "gap":
                gaps += 1
                if eligible_gaps is not None:
                    symbol = event["payload"].get("broker_symbol")
                    windows = (
                        (manifest.sessions[symbol],) if symbol in manifest.sessions else manifest.sessions.values()
                    )
                    occurred = datetime.fromisoformat(event["at"])
                    if any(window.opened_at <= occurred < window.closed_at for window in windows):
                        eligible_gaps += 1
            elif event["kind"] == "decision":
                decisions += 1
                if event["payload"].get("status") != "ready":
                    reasons[event["payload"].get("reason") or "unspecified"] += 1
                elif (
                    manifest is not None
                    and event["payload"].get("broker_symbol") in manifest.rules
                    and manifest.rules[event["payload"]["broker_symbol"]].selection_hash == "0" * 64
                ):
                    # Derived from the durable decision, so a crash cannot lose
                    # the diagnostic paper-abstention explanation.
                    reasons["selection_and_cost_evidence_missing"] += 1
    for event in journal.events():
        if event.occurred_at > as_of:
            if ignore_after_as_of:
                continue
            raise ValueError("journal contains future event relative to report")
        if session_journal is None and event.kind in {"no_trade", "opened", "managed", "closed"}:
            decisions += 1
        if event.kind == "feed_gap":
            gaps += 1
            if eligible_gaps is not None and any(
                window.opened_at <= event.occurred_at < window.closed_at for window in manifest.sessions.values()
            ):
                eligible_gaps += 1
        elif event.kind == "no_trade":
            reasons[event.payload.get("reason", "unspecified")] += 1
        elif event.kind == "opened":
            symbol = event.payload["broker_symbol"]
            if symbol in opens:
                raise ValueError("paper open overlaps unresolved position")
            opens[symbol] = event.payload
        elif event.kind == "closed":
            symbol = event.payload["broker_symbol"]
            if symbol not in opens:
                raise ValueError("paper close has no matching open")
            opened = opens.pop(symbol)
            close = {**event.payload, "closed_at": event.occurred_at.isoformat()}
            required_cost_fields = {"net_pnl_gbp", "gross_pnl_gbp", "commission_gbp", "financing_gbp"}
            if "conversion_fee_fraction" in opened:
                required_cost_fields.add("conversion_fee_gbp")
            if not required_cost_fields <= close.keys():
                raise ValueError("closed paper trade lacks itemized costs")
            expected_net = (
                D(close["gross_pnl_gbp"])
                - D(close["commission_gbp"])
                - D(close["financing_gbp"])
                - D(close.get("conversion_fee_gbp", "0"))
            )
            if D(close["net_pnl_gbp"]) != expected_net:
                raise ValueError("paper trade costs do not reconcile")
            record = TradeRecord(open=opened, close=close)
            trades.append(record)
            pnl = D(close["net_pnl_gbp"])
            daily[event.occurred_at.date().isoformat()] += pnl
            equity_delta += pnl
            peak_delta = max(peak_delta, equity_delta)
            maximum_drawdown = max(maximum_drawdown, peak_delta - equity_delta)
    gross = sum((D(item.close["gross_pnl_gbp"]) for item in trades), D(0))
    commission = sum((D(item.close["commission_gbp"]) for item in trades), D(0))
    financing = sum((D(item.close["financing_gbp"]) for item in trades), D(0))
    conversion_fee = sum((D(item.close.get("conversion_fee_gbp", "0")) for item in trades), D(0))
    net = sum((D(item.close["net_pnl_gbp"]) for item in trades), D(0))
    winners = [D(item.close["net_pnl_gbp"]) for item in trades if D(item.close["net_pnl_gbp"]) > 0]
    losers = [D(item.close["net_pnl_gbp"]) for item in trades if D(item.close["net_pnl_gbp"]) < 0]
    group_rows: dict[tuple[str, str, str, str], list[Decimal]] = defaultdict(list)
    for item in trades:
        date = datetime.fromisoformat(item.open["opened_at"]).date().isoformat()
        key = (item.open["broker_symbol"], item.open["strategy_id"], item.open["direction"], date)
        group_rows[key].append(D(item.close["net_pnl_gbp"]))
    groups = tuple(
        ReportGroup(
            broker_symbol=symbol,
            strategy_id=rule,
            direction=direction,
            session_date=date,
            closed_trades=len(values),
            net_pnl_gbp=sum(values, D(0)),
            win_rate=D(sum(value > 0 for value in values)) / len(values),
        )
        for (symbol, rule, direction, date), values in sorted(group_rows.items())
    )
    dates = sorted(daily)
    coverage_status = "not_measured"
    account_quote_coverage = None
    total_slots = covered_slots = 0
    if manifest is not None and session_journal is not None:
        for symbol, window in manifest.sessions.items():
            slot_samples: dict[int, list[datetime]] = defaultdict(list)
            for event in session_events:
                if (
                    event["kind"] != "quote"
                    or datetime.fromisoformat(event["at"]) > as_of
                    or event["payload"]["broker_symbol"] != symbol
                    or event["payload"]["tradeable"] != "True"
                ):
                    continue
                observed = datetime.fromisoformat(event["payload"]["observed_at"])
                if window.opened_at <= observed < min(window.closed_at, as_of):
                    index = (observed - window.opened_at) // timedelta(minutes=5)
                    slot_samples[index].append(observed)
            slot = window.opened_at
            index = 0
            while slot + timedelta(minutes=5) <= min(window.closed_at, as_of):
                total_slots += 1
                sampled = sorted(slot_samples.get(index, ()))
                if (
                    sampled
                    and sampled[0] - slot <= timedelta(seconds=15)
                    and slot + timedelta(minutes=5) - sampled[-1] <= timedelta(seconds=15)
                    and all(
                        right - left <= timedelta(seconds=30) for left, right in zip(sampled, sampled[1:], strict=False)
                    )
                ):
                    covered_slots += 1
                slot += timedelta(minutes=5)
                index += 1
        if total_slots:
            coverage_status = "measured"
            account_quote_coverage = D(covered_slots) / D(total_slots)
    lower = (
        _daily_block_lower(tuple(daily[day] for day in dates))
        if len(dates) >= 90
        and len(trades) >= 100
        and as_of - started_at >= timedelta(days=90)
        and coverage_status == "measured"
        and account_quote_coverage is not None
        and account_quote_coverage >= D("0.99")
        and eligible_gaps == 0
        and manifest is not None
        and as_of >= max(window.closed_at for window in manifest.sessions.values())
        else None
    )
    return PaperDecisionReport(
        round_id=round_id,
        started_at=started_at,
        generated_at=as_of,
        declared_session_end_at=max(window.closed_at for window in manifest.sessions.values()) if manifest else None,
        rule_identity_hash=(
            canonical_hash(manifest.model_dump(mode="json", exclude={"round_id", "sessions"}))
            if manifest is not None and all(rule.selection_hash != "0" * 64 for rule in manifest.rules.values())
            else None
        ),
        coverage_status=coverage_status,
        account_quote_coverage=account_quote_coverage,
        expected_quote_intervals=total_slots if coverage_status == "measured" else None,
        covered_quote_intervals=covered_slots if coverage_status == "measured" else None,
        decisions_count=decisions,
        no_trade_count=sum(reasons.values()),
        blocked_reasons=dict(reasons),
        feed_gap_count=gaps,
        eligible_session_gap_count=eligible_gaps,
        closed_trades=len(trades),
        open_positions=len(opens),
        gross_pnl_gbp=gross,
        commission_gbp=commission,
        financing_gbp=financing,
        conversion_fee_gbp=conversion_fee,
        net_pnl_gbp=net,
        win_rate=D(len(winners)) / len(trades) if trades else None,
        net_expectancy_gbp=net / len(trades) if trades else None,
        profit_factor=sum(winners, D(0)) / -sum(losers, D(0)) if losers else None,
        maximum_drawdown_gbp=maximum_drawdown,
        daily_block_lower_95_gbp=lower,
        groups=groups,
        trades=tuple(trades),
        unresolved_positions=tuple(opens.values()),
    )


def aggregate_reports(
    reports: list[PaperDecisionReport] | tuple[PaperDecisionReport, ...], *, as_of: datetime
) -> PaperDecisionReport:
    """Combine retained daily rounds without resetting losses or claiming measured coverage."""
    if not reports:
        raise ValueError("at least one retained round required")
    ordered = sorted(reports, key=lambda item: item.started_at)
    if len({item.round_id for item in ordered}) != len(ordered):
        raise ValueError("duplicate retained round")
    if any(item.generated_at > as_of for item in ordered):
        raise ValueError("future retained report")
    trades = tuple(item for report in ordered for item in report.trades)
    unresolved = tuple(item for report in ordered for item in report.unresolved_positions)
    reasons = Counter[str]()
    for report in ordered:
        reasons.update(report.blocked_reasons)
    gross = sum((report.gross_pnl_gbp for report in ordered), D(0))
    commission = sum((report.commission_gbp for report in ordered), D(0))
    financing = sum((report.financing_gbp for report in ordered), D(0))
    conversion_fee = sum((report.conversion_fee_gbp for report in ordered), D(0))
    net = gross - commission - financing - conversion_fee
    ordered_trades = sorted(trades, key=lambda item: datetime.fromisoformat(item.close["closed_at"]))
    running = peak = drawdown = D(0)
    for item in ordered_trades:
        running += D(item.close["net_pnl_gbp"])
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
    winners = [D(item.close["net_pnl_gbp"]) for item in trades if D(item.close["net_pnl_gbp"]) > 0]
    losers = [D(item.close["net_pnl_gbp"]) for item in trades if D(item.close["net_pnl_gbp"]) < 0]
    expected_intervals = (
        sum(item.expected_quote_intervals for item in ordered)
        if all(item.expected_quote_intervals is not None for item in ordered)
        else None
    )
    covered_intervals = (
        sum(item.covered_quote_intervals for item in ordered)
        if all(item.covered_quote_intervals is not None for item in ordered)
        else None
    )
    coverage = D(covered_intervals) / D(expected_intervals) if expected_intervals else None
    eligible_gaps = (
        sum(item.eligible_session_gap_count for item in ordered)
        if all(item.eligible_session_gap_count is not None for item in ordered)
        else None
    )
    shared_rule = ordered[0].rule_identity_hash
    if shared_rule is None or any(item.rule_identity_hash != shared_rule for item in ordered):
        shared_rule = None
    round_dates = [item.started_at.date() for item in ordered]
    calendar_days = (as_of.date() - round_dates[0]).days + 1
    complete_calendar = len(ordered) == calendar_days and len(set(round_dates)) == calendar_days
    completed_sessions = all(
        item.declared_session_end_at is not None and item.generated_at >= item.declared_session_end_at
        for item in ordered
    )
    daily = {day: D(0) for day in round_dates}
    for trade in trades:
        day = datetime.fromisoformat(trade.close["closed_at"]).date()
        if day not in daily:
            complete_calendar = False
        else:
            daily[day] += D(trade.close["net_pnl_gbp"])
    lower = (
        _daily_block_lower(tuple(daily[day] for day in sorted(daily)))
        if complete_calendar
        and completed_sessions
        and as_of - ordered[0].started_at >= timedelta(days=90)
        and len(trades) >= 100
        and coverage is not None
        and coverage >= D("0.99")
        and eligible_gaps == 0
        and shared_rule is not None
        else None
    )
    return PaperDecisionReport(
        round_id="all-retained-practice-rounds",
        started_at=ordered[0].started_at,
        generated_at=as_of,
        declared_session_end_at=(max(item.declared_session_end_at for item in ordered) if completed_sessions else None),
        rule_identity_hash=shared_rule,
        coverage_status="measured" if coverage is not None else "not_measured",
        account_quote_coverage=coverage,
        expected_quote_intervals=expected_intervals,
        covered_quote_intervals=covered_intervals,
        decisions_count=sum(item.decisions_count for item in ordered),
        no_trade_count=sum(item.no_trade_count for item in ordered),
        blocked_reasons=dict(reasons),
        feed_gap_count=sum(item.feed_gap_count for item in ordered),
        eligible_session_gap_count=eligible_gaps,
        closed_trades=len(trades),
        open_positions=len(unresolved),
        gross_pnl_gbp=gross,
        commission_gbp=commission,
        financing_gbp=financing,
        conversion_fee_gbp=conversion_fee,
        net_pnl_gbp=net,
        win_rate=D(len(winners)) / len(trades) if trades else None,
        net_expectancy_gbp=net / len(trades) if trades else None,
        profit_factor=sum(winners, D(0)) / -sum(losers, D(0)) if losers else None,
        maximum_drawdown_gbp=drawdown,
        daily_block_lower_95_gbp=lower,
        groups=tuple(group for report in ordered for group in report.groups),
        trades=trades,
        unresolved_positions=unresolved,
        warning=(
            "Cumulative hypothetical practice results. Declared session hours remain provisional; "
            "account quotes do not prove broker fills or real-money performance."
        ),
    )
