from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.config.settings import Settings
from src.contextual.repository import ContextualRepository
from src.contextual.service import ContextualResearchService, ContextualRunRequest
from src.database.engine import Database
from src.ingestion.bars import MarketBar
from src.strategies.datasets import BarRepository
from src.strategies.types import BarInterval, StrategyMode, canonical_hash

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STRATEGIES = (
    "ema_adx_trend",
    "macd_histogram_trend",
    "rsi_reversal",
    "bollinger_keltner_squeeze",
)


def seed_contextual_market(database: Database, *, count: int = 160) -> datetime:
    start = datetime(2026, 8, 1, tzinfo=UTC)
    bars: list[MarketBar] = []
    price = 100.0
    for index in range(count):
        opened_at = start + timedelta(minutes=5 * index)
        closed_at = opened_at + timedelta(minutes=5)
        next_price = price * (1.0 + 0.0004 + math.sin(index / 11) * 0.0002)
        bars.append(
            MarketBar(
                provider="binance",
                feed="spot",
                symbol="BTCUSDT",
                interval=BarInterval.FIVE_MINUTES,
                open_timestamp=opened_at,
                close_timestamp=closed_at,
                available_at=closed_at,
                open=price,
                high=max(price, next_price) * 1.001,
                low=min(price, next_price) * 0.999,
                close=next_price,
                volume=200_000 + index * 100,
                quote_volume=(200_000 + index * 100) * next_price,
                payload_hash=canonical_hash(["contextual-service", index, next_price]),
            )
        )
        price = next_price
    BarRepository(database).append(bars)

    repository = ContextualRepository(database, clock=lambda: bars[-1].available_at)
    outcomes = []
    for strategy_index, strategy_id in enumerate(STRATEGIES):
        for index, bar in enumerate(bars[20:140]):
            gross = 0.0015 + 0.0002 * math.sin(index / (5 + strategy_index))
            cost = 0.0001
            outcomes.append(
                {
                    "dataset_hash": "dataset-v1",
                    "protocol_hash": "protocol-v1",
                    "code_hash": "code-v1",
                    "config_hash": "config-v1",
                    "source_decision_hash": canonical_hash([strategy_id, index]),
                    "provider": "binance",
                    "feed": "spot",
                    "venue": "Binance",
                    "product": "spot",
                    "asset_class": "crypto",
                    "profile": "crypto_major_spot",
                    "symbol": "BTCUSDT",
                    "interval": "5m",
                    "direction": "long",
                    "mode": "paper",
                    "strategy_id": strategy_id,
                    "decision_timestamp": bar.open_timestamp,
                    "outcome_available_at": bar.available_at,
                    "gross_return": gross,
                    "modeled_cost": cost,
                    "net_return": gross - cost,
                    "regime_probabilities": {
                        "trend_normal": 0.55,
                        "trend_elevated_volatility": 0.15,
                        "range_liquid": 0.25,
                        "stressed_or_illiquid": 0.05,
                    },
                }
            )
    repository.append_outcome_rows(outcomes)
    return bars[-1].available_at


def contextual_service(tmp_path: Path) -> tuple[ContextualResearchService, Database, datetime]:
    database = Database.from_url(f"duckdb:///{tmp_path / 'contextual-service.duckdb'}")
    database.initialize()
    as_of = seed_contextual_market(database)
    settings = Settings.load(PROJECT_ROOT, mode="test").model_copy(
        update={"database_url": f"duckdb:///{tmp_path / 'contextual-service.duckdb'}"}
    )
    return ContextualResearchService(database, settings), database, as_of


def test_evaluate_contexts_emits_ordered_stages_and_persists_cash_safe_result(
    tmp_path: Path,
) -> None:
    service, database, as_of = contextual_service(tmp_path)
    events = []
    request = ContextualRunRequest(
        symbols=("BTCUSDT",),
        provider="binance",
        feed="spot",
        interval=BarInterval.FIVE_MINUTES,
        mode=StrategyMode.PAPER,
        as_of=as_of,
    )

    result = service.evaluate_contexts(request, events.append)

    assert [event.stage for event in events] == [
        "eligibility",
        "regimes",
        "hierarchy",
        "covariance",
        "allocation",
        "portfolio",
    ]
    assert result.portfolio.cash_weight == 1.0
    assert result.portfolio.status == "all_cash"
    assert database.scalar("select count(*) from contextual_estimates") > 0
    assert database.scalar("select count(*) from contextual_weights") == len(STRATEGIES)
    assert database.scalar("select count(*) from portfolio_research_decisions") == 1
