"""Chronological, all-attempt-retained historical research workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.intraday.contracts import ConfirmedBar, InstrumentSpec
from src.intraday.historical import ReplayCosts, replay_session
from src.intraday.research import ResearchCatalog, _daily_block_lower
from src.strategies.types import canonical_hash

D = Decimal


@dataclass(frozen=True)
class HistoricalSession:
    opened_at: datetime
    closed_at: datetime
    bars: tuple[ConfirmedBar, ...]

    def __post_init__(self):
        if self.closed_at <= self.opened_at or not self.bars:
            raise ValueError("session needs bars and positive window")
        if self.bars[0].start != self.opened_at or self.bars[-1].end != self.closed_at:
            raise ValueError("session must have complete boundary coverage")
        if any(a.end != b.start for a, b in zip(self.bars, self.bars[1:], strict=False)):
            raise ValueError("session historical gap")
        if any(bar.instrument != self.bars[0].instrument for bar in self.bars):
            raise ValueError("session mixed instruments")


@dataclass(frozen=True)
class HistoricalStageReport:
    stage: str
    strategy_id: str
    broker_symbol: str
    net_pnl: Decimal
    stressed_net_pnl: Decimal
    lower_daily_net_pnl: Decimal
    closed_trades: int
    cash_baseline: Decimal
    account_specific_fills: bool
    attempt_hash: str


def evaluate_stage(
    catalog: ResearchCatalog,
    stage: str,
    strategy_id: str,
    instrument: InstrumentSpec,
    sessions: tuple[HistoricalSession, ...],
    costs: ReplayCosts,
) -> HistoricalStageReport:
    if stage == "train":
        start, end = catalog.protocol.train_start, catalog.protocol.validation_start
    elif stage == "validation":
        start, end = catalog.protocol.validation_start, catalog.protocol.sealed_start
    elif stage == "sealed":
        start, end = catalog.protocol.sealed_start, catalog.protocol.sealed_end
    else:
        raise ValueError("unknown stage")
    if not sessions or any(not start <= session.opened_at < session.closed_at <= end for session in sessions):
        raise ValueError("session outside stage window")
    if any(session.bars[0].instrument != instrument for session in sessions):
        raise ValueError("session instrument mismatch")
    if tuple(sorted(sessions, key=lambda item: item.opened_at)) != sessions or any(
        a.closed_at > b.opened_at for a, b in zip(sessions, sessions[1:], strict=False)
    ):
        raise ValueError("overlapping or unsorted sessions")
    spread = max((bar.ask_close - bar.bid_close for session in sessions for bar in session.bars), default=D(0))
    stressed_costs = costs.model_copy(
        update={"slippage_points": costs.slippage_points + spread * (catalog.protocol.stress_spread_multiple - 1) / 2}
    )
    base = [
        replay_session(
            strategy_id, session.bars, session_open=session.opened_at, session_close=session.closed_at, costs=costs
        )
        for session in sessions
    ]
    stressed = [
        replay_session(
            strategy_id,
            session.bars,
            session_open=session.opened_at,
            session_close=session.closed_at,
            costs=stressed_costs,
        )
        for session in sessions
    ]
    net = sum((result.total_net_pnl for result in base), D(0))
    stressed_net = sum((result.total_net_pnl for result in stressed), D(0))
    count = sum(len(result.trades) for result in base)
    source_hash = canonical_hash(
        {
            "stage": stage,
            "sessions": [[bar.model_dump(mode="json") for bar in session.bars] for session in sessions],
            "costs": costs.model_dump(mode="json"),
        }
    )
    attempt = catalog.record_attempt(
        stage, strategy_id, instrument.broker_symbol, net, stressed_net, count, source_hash
    )
    return HistoricalStageReport(
        stage,
        strategy_id,
        instrument.broker_symbol,
        net,
        stressed_net,
        _daily_block_lower(tuple(result.total_net_pnl for result in base)),
        count,
        D(0),
        False,
        attempt.attempt_hash,
    )
