from __future__ import annotations

import os
from datetime import UTC, datetime

from src.config.settings import Settings
from src.database.engine import Database
from src.strategies.types import canonical_hash
from src.trading.alpaca import AlpacaCredentials, AlpacaTradingClient
from src.trading.repository import TradingRepository
from src.trading.risk import PreTradeRiskEngine
from src.trading.shadow import ShadowBrokerClient
from src.trading.supervisor import TradingSupervisor
from src.trading.types import BrokerAccount, BrokerClock


def paper_credentials_from_environment() -> AlpacaCredentials:
    key_id = os.getenv("APCA_API_KEY_ID", "")
    secret_key = os.getenv("APCA_API_SECRET_KEY", "")
    if not key_id or not secret_key:
        raise ValueError("Alpaca paper credentials are required in the process environment")
    return AlpacaCredentials(key_id=key_id, secret_key=secret_key)


def create_paper_supervisor(settings: Settings, *, session_id: str) -> TradingSupervisor:
    credentials = paper_credentials_from_environment()
    database = Database.from_url(settings.database_url)
    database.initialize()
    return TradingSupervisor(
        repository=TradingRepository(database),
        broker=AlpacaTradingClient(credentials),
        session_id=session_id,
        cohort_hash="unassigned-paper-cohort",
        provider="alpaca",
        feed="iex",
        interval="1Min",
        strategy_version="unassigned",
        code_hash=canonical_hash("local-engine"),
        config_hash=canonical_hash(settings.config_hash_payload()),
        risk_engine=PreTradeRiskEngine(settings.trading.risk),
    )


def create_shadow_supervisor(settings: Settings, *, session_id: str) -> TradingSupervisor:
    now = datetime.now(UTC)
    account = BrokerAccount(
        account_id="shadow-account",
        account_suffix="adow",
        status="ACTIVE",
        equity="100000",
        buying_power="100000",
        trading_blocked=False,
        pattern_day_trader=False,
        shorting_enabled=True,
        received_at=now,
    )
    broker = ShadowBrokerClient(
        account=account,
        clock=BrokerClock(timestamp=now, is_open=True, next_open=now, next_close=now, received_at=now),
    )
    database = Database.from_url(settings.database_url)
    database.initialize()
    return TradingSupervisor(
        repository=TradingRepository(database),
        broker=broker,
        session_id=session_id,
        cohort_hash="unassigned-shadow-cohort",
        provider="local",
        feed="shadow",
        interval="1Min",
        strategy_version="unassigned",
        code_hash=canonical_hash("local-engine"),
        config_hash=canonical_hash(settings.config_hash_payload()),
    )


def trading_status(settings: Settings) -> dict[str, object]:
    database = Database.from_url(settings.database_url)
    database.initialize()
    frame = database.frame(
        "SELECT environment, account_suffix, status, started_at, ended_at, terminal_reason "
        "FROM broker_sessions ORDER BY started_at DESC LIMIT 1"
    )
    if frame.empty:
        return {"environment": "none", "status": "not_started", "live": "locked"}
    row = frame.iloc[0]
    return {
        "environment": str(row.environment),
        "account_suffix": str(row.account_suffix),
        "status": str(row.status),
        "started_at": row.started_at.isoformat(),
        "ended_at": None if row.ended_at is None else row.ended_at.isoformat(),
        "terminal_reason": row.terminal_reason,
        "live": "locked",
    }


__all__ = [
    "create_paper_supervisor",
    "create_shadow_supervisor",
    "paper_credentials_from_environment",
    "trading_status",
]
