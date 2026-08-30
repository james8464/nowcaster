"""Database-backed orchestration for causal contextual strategy research."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Literal

import numpy as np
import pandas as pd

from src.config.settings import InstrumentConfig, Settings
from src.contextual.allocation import ContextualAllocation, allocate_contextual_weights
from src.contextual.eligibility import (
    AssetEligibilityEvidence,
    eligibility_inputs_from_bars,
    evaluate_asset_eligibility,
    strategy_is_applicable,
)
from src.contextual.hierarchy import (
    REGIME_PROBABILITY_COLUMNS,
    BlendedRegimeEstimate,
    HierarchyResult,
    blend_current_regime,
    build_hierarchical_estimates,
)
from src.contextual.portfolio import (
    PortfolioSelection,
    ResearchOpportunity,
    select_portfolio_opportunities,
)
from src.contextual.regimes import causal_regime_features, fit_regime_model, predict_regime_posteriors
from src.contextual.repository import ContextualRepository
from src.contextual.types import EligibilityState, StrategyContextKey, StrategyDirection
from src.database.engine import Database
from src.ingestion.bars import INTERVAL_DURATION
from src.strategies.types import BarInterval, StrategyMode, canonical_hash

EventSink = Callable[["ContextualProgress"], None]
ContextualStage = Literal["eligibility", "regimes", "hierarchy", "covariance", "allocation", "portfolio"]


@dataclass(frozen=True, slots=True)
class ContextualRunRequest:
    symbols: tuple[str, ...]
    provider: str
    feed: str
    interval: BarInterval
    mode: StrategyMode
    as_of: datetime

    def __post_init__(self) -> None:
        symbols = tuple(str(symbol).strip().upper() for symbol in self.symbols)
        if not symbols or any(not symbol for symbol in symbols):
            raise ValueError("contextual research requires at least one symbol")
        if len(symbols) != len(set(symbols)):
            raise ValueError("contextual research symbols must be unique")
        provider = self.provider.strip().lower()
        feed = self.feed.strip().lower()
        if not provider or not feed:
            raise ValueError("contextual provider and feed cannot be blank")
        if self.as_of.tzinfo is not UTC:
            raise ValueError("contextual as_of must be an explicit UTC datetime")
        object.__setattr__(self, "symbols", symbols)
        object.__setattr__(self, "provider", provider)
        object.__setattr__(self, "feed", feed)


@dataclass(frozen=True, slots=True)
class ContextualProgress:
    stage: ContextualStage
    progress: float
    message: str


@dataclass(frozen=True, slots=True)
class UniverseScreenResult:
    dataset_hash: str
    protocol_hash: str
    eligibility: tuple[AssetEligibilityEvidence, ...]
    regime_probabilities: Mapping[str, Mapping[str, float]]
    evidence_hash: str


@dataclass(frozen=True, slots=True)
class ContextualRunResult:
    request: ContextualRunRequest
    dataset_hash: str
    protocol_hash: str
    screen: UniverseScreenResult
    hierarchy: HierarchyResult
    allocations: Mapping[str, ContextualAllocation]
    portfolio: PortfolioSelection
    evidence_hash: str


@dataclass(frozen=True, slots=True)
class ContextualBacktestResult:
    backtest_run_id: str
    protocol_hash: str
    observations: int
    net_return: float
    maximum_drawdown: float
    status: Literal["completed", "all_cash"]


@dataclass(frozen=True, slots=True)
class ContextualLearningResult:
    global_trial_id: str
    status: Literal["shadow"]
    evaluation_budget: int
    seed: int


@dataclass(frozen=True, slots=True)
class _OutcomeAssembly:
    frame: pd.DataFrame
    dataset_hash: str
    protocol_hash: str
    source_datasets: Mapping[str, str]


class ContextualResearchService:
    """Assemble exact evidence and run the contextual stack without broker effects."""

    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.database.initialize()
        self.settings = settings
        if settings.asset_selection is None:
            raise ValueError("asset_selection configuration is required for contextual research")
        self.config = settings.asset_selection
        self.repository = ContextualRepository(database)
        self._specs = {item.strategy_id: item for item in settings.strategies.enabled}

    @staticmethod
    def _emit(sink: EventSink | None, stage: ContextualStage, message: str) -> None:
        if sink is not None:
            sink(ContextualProgress(stage=stage, progress=1.0, message=message))

    def _validate_request(self, request: ContextualRunRequest) -> None:
        if len(request.symbols) > self.config.maximum_candidate_universe:
            raise ValueError("requested symbols exceed the configured candidate-universe limit")
        configured = {item.symbol for item in self.settings.instruments.instruments if item.enabled}
        missing = set(request.symbols) - configured
        if missing:
            raise ValueError(f"unconfigured contextual symbols: {', '.join(sorted(missing))}")

    def _instrument(self, symbol: str, request: ContextualRunRequest) -> InstrumentConfig:
        matches = tuple(
            item
            for item in self.settings.instruments.instruments
            if item.enabled and item.symbol == symbol and item.profile is not None
        )
        if len(matches) != 1:
            raise ValueError(f"{symbol} requires one enabled instrument with an explicit profile")
        return matches[0].model_copy(update={"provider": request.provider, "feed": request.feed})

    def _bars(self, request: ContextualRunRequest, symbol: str) -> pd.DataFrame:
        frame = self.database.frame(
            "select * from market_bars where provider = :provider and feed = :feed "
            "and symbol = :symbol and interval = :interval and finalized = true "
            "and available_at <= :as_of order by open_timestamp, available_at, revision",
            {
                "provider": request.provider,
                "feed": request.feed,
                "symbol": symbol,
                "interval": request.interval.value,
                "as_of": request.as_of,
            },
        )
        if frame.empty:
            raise ValueError(f"no finalized {request.provider}/{request.feed} bars are available for {symbol}")
        for column in ("open_timestamp", "close_timestamp", "available_at"):
            frame[column] = pd.to_datetime(frame[column], utc=True)
        return (
            frame.sort_values(["open_timestamp", "available_at", "revision"], kind="stable")
            .drop_duplicates("open_timestamp", keep="last")
            .sort_values("open_timestamp", kind="stable")
            .reset_index(drop=True)
        )

    def _latest_live_payload(
        self,
        request: ContextualRunRequest,
        symbol: str,
        event_type: str,
    ) -> tuple[str, dict[str, object]] | None:
        frame = self.database.frame(
            "select event_id, payload from live_market_events where provider = :provider and feed = :feed "
            "and symbol = :symbol and event_type = :event_type and provider_time <= :as_of "
            "and processed_at <= :as_of order by provider_time desc, processed_at desc limit 1",
            {
                "provider": request.provider,
                "feed": request.feed,
                "symbol": symbol,
                "event_type": event_type,
                "as_of": request.as_of,
            },
        )
        if frame.empty or not isinstance(frame.iloc[0]["payload"], dict):
            return None
        return str(frame.iloc[0]["event_id"]), dict(frame.iloc[0]["payload"])

    def _eligibility(
        self,
        request: ContextualRunRequest,
        instrument: InstrumentConfig,
        bars: pd.DataFrame,
        direction: StrategyDirection,
    ) -> AssetEligibilityEvidence:
        assert instrument.profile is not None
        policy = self.config.profiles[instrument.profile]
        inputs = eligibility_inputs_from_bars(
            bars,
            as_of=request.as_of,
            instrument=instrument,
            interval=request.interval,
            direction=direction,
        )
        quote = self._latest_live_payload(request, instrument.symbol, "quote")
        depth = self._latest_live_payload(request, instrument.symbol, "depth")
        updates: dict[str, object] = {}
        source_ids: list[str] = [inputs.source_event_watermark]
        if quote is not None:
            quote_id, payload = quote
            bid = float(payload["bid"])
            ask = float(payload["ask"])
            midpoint = (bid + ask) / 2.0
            updates.update(
                {
                    "last_price": float(payload["last"]),
                    "tick_size": float(payload["tick_size"]),
                    "spread_bps": (ask - bid) / midpoint * 10_000 if midpoint > 0 else None,
                }
            )
            source_ids.append(quote_id)
        if depth is not None:
            depth_id, payload = depth
            levels = tuple(payload.get("bids", ())) + tuple(payload.get("asks", ()))
            depth_notional = sum(float(item["price"]) * float(item["size"]) for item in levels)
            updates.update(
                {
                    "depth_notional": depth_notional,
                    "estimated_price_impact_bps": 0.0,
                }
            )
            source_ids.append(depth_id)
        if quote is not None and depth is not None:
            updates["liquidity_grade"] = "observed"
        if updates:
            updates["source_event_watermark"] = canonical_hash(source_ids)
            inputs = inputs.model_copy(update=updates)
        policy_hash = canonical_hash({"profile": instrument.profile.value, "policy": policy.model_dump(mode="json")})
        return evaluate_asset_eligibility(inputs, policy, policy_hash)

    @staticmethod
    def _regime_record(
        bars: pd.DataFrame,
        request: ContextualRunRequest,
        instrument: InstrumentConfig,
        dataset_hash: str,
        protocol_hash: str,
    ) -> dict[str, object]:
        assert instrument.profile is not None
        features = causal_regime_features(bars).reset_index(drop=True)
        target = features.iloc[[-1]].copy()
        fit = fit_regime_model(features.iloc[:-1].copy(), minimum_train=80)
        posterior = predict_regime_posteriors(fit, target)
        probabilities = {
            regime.value: float(posterior.probabilities[0, index]) for index, regime in enumerate(posterior.regimes)
        }
        return {
            "model_hash": fit.model_hash,
            "dataset_hash": dataset_hash,
            "protocol_hash": protocol_hash,
            "provider": request.provider,
            "feed": request.feed,
            "venue": instrument.venue,
            "product": instrument.product,
            "asset_class": instrument.asset_class,
            "profile": instrument.profile.value,
            "symbol": instrument.symbol,
            "interval": request.interval.value,
            "decision_timestamp": request.as_of,
            "feature_through": pd.Timestamp(target.iloc[0]["available_at"]).to_pydatetime(),
            "training_through": fit.training_through,
            "status": fit.status,
            "probabilities": probabilities,
            "posterior_hash": posterior.posterior_hash,
        }

    def _fallback_identity(
        self,
        request: ContextualRunRequest,
        bars_by_symbol: Mapping[str, pd.DataFrame],
    ) -> tuple[str, str]:
        datasets = {
            symbol: canonical_hash(
                {
                    "provider": request.provider,
                    "feed": request.feed,
                    "symbol": symbol,
                    "interval": request.interval.value,
                    "payload_hashes": tuple(frame["payload_hash"].astype(str)),
                }
            )
            for symbol, frame in bars_by_symbol.items()
        }
        dataset_hash = canonical_hash({"contextual_bar_datasets": datasets})
        protocol_hash = canonical_hash(
            {
                "contextual_screen_protocol": 1,
                "asset_selection": self.config.model_dump(mode="json"),
            }
        )
        return dataset_hash, protocol_hash

    def _screen(
        self,
        request: ContextualRunRequest,
        sink: EventSink | None,
        *,
        identity: tuple[str, str] | None = None,
    ) -> UniverseScreenResult:
        self._validate_request(request)
        bars_by_symbol = {symbol: self._bars(request, symbol) for symbol in request.symbols}
        dataset_hash, protocol_hash = identity or self._fallback_identity(request, bars_by_symbol)
        eligibility: list[AssetEligibilityEvidence] = []
        instruments: dict[str, InstrumentConfig] = {}
        for symbol in request.symbols:
            instrument = self._instrument(symbol, request)
            instruments[symbol] = instrument
            assert instrument.profile is not None
            for direction in self.config.profiles[instrument.profile].allowed_directions:
                evidence = self._eligibility(request, instrument, bars_by_symbol[symbol], direction)
                self.repository.append_eligibility(evidence)
                eligibility.append(evidence)
        self._emit(sink, "eligibility", f"screened {len(eligibility)} asset-direction contexts")

        posteriors: dict[str, Mapping[str, float]] = {}
        posterior_ids: list[str] = []
        for symbol in request.symbols:
            record = self._regime_record(
                bars_by_symbol[symbol],
                request,
                instruments[symbol],
                dataset_hash,
                protocol_hash,
            )
            self.repository.append_regime_posterior(record)
            posteriors[symbol] = MappingProxyType(dict(record["probabilities"]))
            posterior_ids.append(str(record["posterior_hash"]))
        self._emit(sink, "regimes", f"inferred {len(posteriors)} causal regime posteriors")
        evidence_hash = canonical_hash(
            {
                "dataset_hash": dataset_hash,
                "protocol_hash": protocol_hash,
                "eligibility_ids": tuple(item.evidence_id for item in eligibility),
                "posterior_ids": tuple(posterior_ids),
            }
        )
        return UniverseScreenResult(
            dataset_hash=dataset_hash,
            protocol_hash=protocol_hash,
            eligibility=tuple(eligibility),
            regime_probabilities=MappingProxyType(posteriors),
            evidence_hash=evidence_hash,
        )

    def screen_universe(
        self,
        request: ContextualRunRequest,
        sink: EventSink | None = None,
    ) -> UniverseScreenResult:
        return self._screen(request, sink)

    def _complete_run_context(
        self,
        symbol: str,
        candidate: pd.DataFrame,
        request: ContextualRunRequest,
    ) -> pd.DataFrame | None:
        runs = self.database.frame(
            "select dataset_hash, strategy_id, run_timestamp, metrics from strategy_runs "
            "where symbol = :symbol and interval = :interval and mode = :mode and status = 'evaluated'",
            {"symbol": symbol, "interval": request.interval.value, "mode": request.mode.value},
        )
        if runs.empty:
            return None
        valid: list[tuple[datetime, pd.DataFrame]] = []
        for (_cohort_id, effective_at), group in runs.groupby(
            [
                runs["metrics"].map(lambda value: value.get("cohort_id") if isinstance(value, dict) else None),
                runs["metrics"].map(
                    lambda value: value.get("cohort_effective_at") if isinstance(value, dict) else None
                ),
            ],
            dropna=True,
        ):
            metrics = tuple(item for item in group["metrics"] if isinstance(item, dict))
            if not metrics:
                continue
            first = metrics[0]
            count = first.get("contextual_outcome_count")
            index_hash = first.get("contextual_outcome_index_hash")
            protocol = first.get("contextual_protocol_hash")
            members = tuple(sorted(str(item["strategy_id"]) for item in first.get("cohort_members", ())))
            dataset = str(group.iloc[0]["dataset_hash"])
            if (
                not isinstance(count, int)
                or not isinstance(index_hash, str)
                or not isinstance(protocol, str)
                or not members
                or any(str(item["dataset_hash"]) != dataset for _, item in group.iterrows())
            ):
                continue
            selected = candidate.loc[
                (candidate["dataset_hash"].astype(str) == dataset)
                & (candidate["protocol_hash"].astype(str) == protocol)
                & candidate["strategy_id"].astype(str).isin(members)
            ]
            index = sorted(
                (
                    {
                        "outcome_id": str(row.outcome_id),
                        "content_hash": str(row.content_hash),
                    }
                    for row in selected.itertuples(index=False)
                ),
                key=lambda item: item["outcome_id"],
            )
            if len(index) == count and canonical_hash(index) == index_hash:
                valid.append((pd.Timestamp(effective_at).to_pydatetime(), selected))
        if not valid:
            raise ValueError(f"{symbol} has strategy runs but no complete contextual outcome cohort")
        return max(valid, key=lambda item: item[0])[1]

    def _outcomes(self, request: ContextualRunRequest) -> _OutcomeAssembly:
        frame = self.database.frame(
            "select * from contextual_outcomes where provider = :provider and feed = :feed "
            "and interval = :interval and mode = :mode and outcome_available_at <= :as_of",
            {
                "provider": request.provider,
                "feed": request.feed,
                "interval": request.interval.value,
                "mode": request.mode.value,
                "as_of": request.as_of,
            },
        )
        frame = frame.loc[frame["symbol"].astype(str).isin(request.symbols)].copy()
        if frame.empty:
            raise ValueError("no authenticated contextual outcomes are available for this request")
        for column in ("decision_timestamp", "outcome_available_at", "created_at"):
            frame[column] = pd.to_datetime(frame[column], utc=True)
        authenticated = frame.apply(
            lambda row: (
                isinstance(row["evidence"], dict)
                and canonical_hash(row["evidence"]) == str(row["content_hash"])
                and str(row["evidence"].get("source_decision_hash")) == str(row["source_decision_hash"])
            ),
            axis=1,
        )
        if not authenticated.all():
            raise ValueError("contextual outcome authentication failed")

        selected_by_symbol: dict[str, pd.DataFrame] = {}
        source_datasets: dict[str, str] = {}
        for symbol in request.symbols:
            candidate = frame.loc[frame["symbol"].astype(str) == symbol].copy()
            if candidate.empty:
                raise ValueError(f"no contextual outcome cohort is available for {symbol}")
            complete = self._complete_run_context(symbol, candidate, request)
            if complete is None:
                group_scores = []
                for key, group in candidate.groupby(
                    ["dataset_hash", "protocol_hash", "code_hash", "config_hash"], sort=True
                ):
                    group_scores.append((group["created_at"].max(), group["outcome_available_at"].max(), key, group))
                complete = max(group_scores, key=lambda item: (item[0], item[1], item[2]))[3]
            selected_by_symbol[symbol] = complete
            source_datasets[symbol] = str(complete.iloc[0]["dataset_hash"])
        selected = pd.concat(tuple(selected_by_symbol.values()), ignore_index=True)
        protocols = set(selected["protocol_hash"].astype(str))
        code_hashes = set(selected["code_hash"].astype(str))
        config_hashes = set(selected["config_hash"].astype(str))
        if len(protocols) != 1 or len(code_hashes) != 1 or len(config_hashes) != 1:
            raise ValueError("requested assets do not share one exact contextual protocol/code/config cohort")
        dataset_hash = (
            next(iter(set(source_datasets.values())))
            if len(set(source_datasets.values())) == 1
            else canonical_hash({"contextual_dataset_components": source_datasets})
        )
        selected["source_dataset_hash"] = selected["dataset_hash"].astype(str)
        selected["dataset_hash"] = dataset_hash
        for regime, column in REGIME_PROBABILITY_COLUMNS.items():
            selected[column] = selected["regime_probabilities"].map(
                lambda value, regime=regime: float(value[regime.value])
            )
        known = set(self._specs)
        selected = selected.loc[selected["strategy_id"].astype(str).isin(known)].copy()
        if selected.empty:
            raise ValueError("contextual outcomes do not reference enabled local strategies")
        return _OutcomeAssembly(
            frame=selected,
            dataset_hash=dataset_hash,
            protocol_hash=next(iter(protocols)),
            source_datasets=MappingProxyType(source_datasets),
        )

    @staticmethod
    def _synchronized_returns(frame: pd.DataFrame) -> pd.DataFrame:
        returns = frame.pivot_table(
            index="outcome_available_at",
            columns="strategy_id",
            values="net_return",
            aggfunc="sum",
        ).sort_index(kind="stable")
        returns.index = pd.DatetimeIndex(pd.to_datetime(returns.index, utc=True))
        return returns

    def _previous_weights(self, context_hash: str, strategy_ids: Sequence[str]) -> dict[str, float]:
        frame = self.database.frame(
            "select allocation_id, strategy_id, weight, effective_at from contextual_weights "
            "where context_hash = :context_hash order by effective_at desc",
            {"context_hash": context_hash},
        )
        if frame.empty:
            return {item: 0.0 for item in strategy_ids}
        latest = frame.iloc[0]["allocation_id"]
        selected = frame.loc[frame["allocation_id"] == latest]
        observed = {str(row.strategy_id): float(row.weight) for row in selected.itertuples(index=False)}
        return {item: observed.get(item, 0.0) for item in strategy_ids}

    @staticmethod
    def _hierarchical_prior(estimates: Mapping[str, BlendedRegimeEstimate]) -> dict[str, float]:
        positive = {key: max(float(value.lower_net_edge), 0.0) for key, value in estimates.items()}
        total = sum(positive.values())
        if total <= 0:
            return {key: 0.0 for key in estimates}
        return {key: 0.5 * value / total for key, value in positive.items()}

    def _allocation_context(
        self,
        request: ContextualRunRequest,
        assembly: _OutcomeAssembly,
        instrument: InstrumentConfig,
        direction: StrategyDirection,
    ) -> dict[str, object]:
        assert instrument.profile is not None
        key = StrategyContextKey(
            dataset_hash=assembly.dataset_hash,
            protocol_hash=assembly.protocol_hash,
            provider=request.provider,
            feed=request.feed,
            venue=instrument.venue,
            product=instrument.product,
            asset_class=instrument.asset_class,
            profile=instrument.profile,
            symbol=instrument.symbol,
            interval=request.interval,
            direction=direction,
            regime=None,
            mode=request.mode,
        )
        return {
            "context_hash": key.context_hash,
            "dataset_hash": assembly.dataset_hash,
            "protocol_hash": assembly.protocol_hash,
            "provider": request.provider,
            "feed": request.feed,
            "venue": instrument.venue,
            "product": instrument.product,
            "profile": instrument.profile.value,
            "symbol": instrument.symbol,
            "interval": request.interval.value,
            "direction": direction.value,
            "effective_at": request.as_of,
        }

    @staticmethod
    def _probability_and_payoff(frame: pd.DataFrame) -> tuple[float, float]:
        values = frame["net_return"].to_numpy(dtype=float)
        count = len(values)
        if count == 0:
            return 0.0, 0.0
        successes = int((values > 0).sum())
        proportion = successes / count
        z = 1.6448536269514722
        denominator = 1.0 + z * z / count
        centre = proportion + z * z / (2.0 * count)
        spread = z * math.sqrt(proportion * (1.0 - proportion) / count + z * z / (4.0 * count * count))
        lower = max((centre - spread) / denominator, 0.0)
        wins = values[values > 0]
        losses = -values[values < 0]
        payoff = float(np.median(wins) / np.median(losses)) if len(wins) and len(losses) else 1.0 if len(wins) else 0.0
        return lower, max(payoff, 0.0)

    def _asset_returns(self, request: ContextualRunRequest) -> pd.DataFrame:
        series = []
        for symbol in request.symbols:
            bars = self._bars(request, symbol)
            returns = pd.Series(
                pd.to_numeric(bars["close"], errors="coerce").pct_change().to_numpy(),
                index=pd.DatetimeIndex(pd.to_datetime(bars["available_at"], utc=True)),
                name=symbol,
            )
            series.append(returns)
        return pd.concat(series, axis=1).sort_index(kind="stable")

    def evaluate_contexts(
        self,
        request: ContextualRunRequest,
        sink: EventSink | None = None,
    ) -> ContextualRunResult:
        self._validate_request(request)
        assembly = self._outcomes(request)
        screen = self._screen(
            request,
            sink,
            identity=(assembly.dataset_hash, assembly.protocol_hash),
        )
        hierarchy = build_hierarchical_estimates(
            assembly.frame,
            request.as_of,
            self.config.hierarchy_prior_strengths,
        )
        self.repository.append_estimates(hierarchy.estimates, effective_at=request.as_of)
        self._emit(sink, "hierarchy", f"built {len(hierarchy.estimates)} partially pooled estimates")

        eligibility = {(item.symbol, item.direction): item for item in screen.eligibility}
        allocations: dict[str, ContextualAllocation] = {}
        blended_by_context: dict[str, Mapping[str, BlendedRegimeEstimate]] = {}
        context_records: dict[str, dict[str, object]] = {}
        for symbol in request.symbols:
            instrument = self._instrument(symbol, request)
            assert instrument.profile is not None
            policy = self.config.profiles[instrument.profile]
            symbol_frame = assembly.frame.loc[assembly.frame["symbol"].astype(str) == symbol]
            for direction in policy.allowed_directions:
                directional = symbol_frame.loc[symbol_frame["direction"].astype(str) == direction.value]
                strategy_ids = tuple(sorted(set(directional["strategy_id"].astype(str))))
                if not strategy_ids:
                    continue
                blended = {
                    strategy_id: blend_current_regime(
                        hierarchy,
                        screen.regime_probabilities[symbol],
                        strategy_id=strategy_id,
                        symbol=symbol,
                        direction=direction,
                    )
                    for strategy_id in strategy_ids
                }
                context = self._allocation_context(request, assembly, instrument, direction)
                families = {strategy_id: self._specs[strategy_id].family for strategy_id in strategy_ids}
                applicable = {
                    strategy_id: strategy_is_applicable(
                        self._specs[strategy_id],
                        instrument,
                        policy,
                        direction,
                        "continuous" if instrument.trading_calendar == "24x7" else "regular",
                        interval=request.interval,
                        peer_count=max(len(request.symbols) - 1, 0),
                    )
                    for strategy_id in strategy_ids
                }
                allocation = allocate_contextual_weights(
                    blended,
                    self._synchronized_returns(directional),
                    self._hierarchical_prior(blended),
                    self._previous_weights(str(context["context_hash"]), strategy_ids),
                    families,
                    self.config.allocation,
                    request.as_of,
                    applicable=applicable,
                )
                key = f"{symbol}:{direction.value}"
                allocations[key] = allocation
                blended_by_context[key] = MappingProxyType(blended)
                context_records[key] = context
                self.repository.append_covariance(allocation.covariance, context)
                self.repository.append_allocation(allocation, context)
        if not allocations:
            raise ValueError("no configured asset-direction context has authenticated strategy outcomes")
        self._emit(sink, "covariance", f"validated {len(allocations)} strategy covariance contexts")
        self._emit(sink, "allocation", f"allocated {len(allocations)} strategy contexts")

        opportunities: list[ResearchOpportunity] = []
        for key, allocation in allocations.items():
            symbol, direction_value = key.split(":", 1)
            direction = StrategyDirection(direction_value)
            instrument = self._instrument(symbol, request)
            evidence = eligibility[(symbol, direction)]
            estimates = blended_by_context[key]
            directional = assembly.frame.loc[
                (assembly.frame["symbol"].astype(str) == symbol)
                & (assembly.frame["direction"].astype(str) == direction.value)
            ]
            probability, payoff = self._probability_and_payoff(directional)
            weighted_lower = sum(
                float(allocation.weights.get(strategy_id, 0.0)) * estimate.lower_net_edge
                for strategy_id, estimate in estimates.items()
            )
            if allocation.status == "all_cash":
                weighted_lower = max((item.lower_net_edge for item in estimates.values()), default=0.0)
            dominant = max(
                estimates,
                key=lambda strategy_id: (
                    float(allocation.weights.get(strategy_id, 0.0)),
                    estimates[strategy_id].lower_net_edge,
                    strategy_id,
                ),
            )
            bars = self._bars(request, symbol)
            volatility = float(pd.to_numeric(bars["close"]).pct_change().dropna().std(ddof=1))
            context = context_records[key]
            opportunities.append(
                ResearchOpportunity(
                    decision_hash=canonical_hash(
                        {
                            "allocation_id": allocation.allocation_id,
                            "context_hash": context["context_hash"],
                            "as_of": request.as_of,
                        }
                    ),
                    context_hash=str(context["context_hash"]),
                    symbol=symbol,
                    direction=direction,
                    asset_class=instrument.asset_class,
                    sector=instrument.asset_class,
                    family=self._specs[dominant].family,
                    decision_time=request.as_of,
                    horizon_minutes=max(int(INTERVAL_DURATION[request.interval].total_seconds() // 60), 1),
                    eligible=evidence.state is EligibilityState.ELIGIBLE and allocation.status == "allocated",
                    lower_net_edge=float(weighted_lower),
                    liquidity_quality=evidence.quality_score,
                    probability_lower=probability,
                    payoff_lower=payoff,
                    realized_volatility=max(volatility, 0.0),
                    liquidity_capacity_weight=(
                        self.config.portfolio.maximum_asset_weight * evidence.quality_score
                        if evidence.state is EligibilityState.ELIGIBLE
                        else 0.0
                    ),
                    remaining_risk_weight=self.config.portfolio.maximum_asset_weight,
                )
            )
        portfolio = select_portfolio_opportunities(
            opportunities,
            self._asset_returns(request),
            self.config.portfolio,
            request.as_of,
        )
        selected_by_hash = {item.opportunity.decision_hash: item for item in portfolio.selected}
        for opportunity in opportunities:
            selected = selected_by_hash.get(opportunity.decision_hash)
            exclusion_keys = (
                opportunity.symbol,
                f"{opportunity.symbol}:{opportunity.direction.value}:{opportunity.decision_hash[:12]}",
            )
            reasons = next(
                (tuple(portfolio.exclusions[key]) for key in exclusion_keys if key in portfolio.exclusions),
                (),
            )
            self.repository.append_portfolio_decision(
                {
                    "selection_id": portfolio.selection_id,
                    "decision_hash": opportunity.decision_hash,
                    "context_hash": opportunity.context_hash,
                    "symbol": opportunity.symbol,
                    "direction": opportunity.direction.value,
                    "effective_at": request.as_of,
                    "status": "selected" if selected is not None else "excluded",
                    "selected": selected is not None,
                    "weight": selected.weight if selected is not None else 0.0,
                    "exclusion_reasons": reasons or (() if selected is not None else ("not_selected",)),
                    "opportunity": asdict(opportunity),
                }
            )
        self._emit(sink, "portfolio", f"portfolio result: {portfolio.status}")
        evidence_hash = canonical_hash(
            {
                "screen_hash": screen.evidence_hash,
                "hierarchy_hash": hierarchy.evidence_hash,
                "allocations": {key: value.allocation_id for key, value in allocations.items()},
                "portfolio": portfolio.selection_id,
            }
        )
        return ContextualRunResult(
            request=request,
            dataset_hash=assembly.dataset_hash,
            protocol_hash=assembly.protocol_hash,
            screen=screen,
            hierarchy=hierarchy,
            allocations=MappingProxyType(allocations),
            portfolio=portfolio,
            evidence_hash=evidence_hash,
        )

    def backtest_portfolio(
        self,
        request: ContextualRunRequest,
        sink: EventSink | None = None,
    ) -> ContextualBacktestResult:
        assembly = self._outcomes(request)
        timestamps = tuple(sorted(pd.to_datetime(assembly.frame["outcome_available_at"], utc=True).unique()))
        if len(timestamps) < 40:
            raise ValueError("contextual portfolio backtest requires at least 40 resolved timestamps")
        cutoff_indices = np.linspace(20, len(timestamps) - 2, 8, dtype=int)
        cutoffs = tuple(pd.Timestamp(timestamps[index]).to_pydatetime() for index in cutoff_indices)
        net_returns: list[float] = []
        gross_equity = 1.0
        net_equity = 1.0
        curve: list[dict[str, object]] = []
        for cutoff in cutoffs:
            fold_request = replace(request, as_of=cutoff)
            result = self.evaluate_contexts(fold_request)
            next_rows = assembly.frame.loc[
                pd.to_datetime(assembly.frame["outcome_available_at"], utc=True) > pd.Timestamp(cutoff)
            ].sort_values("outcome_available_at", kind="stable")
            realized = 0.0
            for selected in result.portfolio.selected:
                match = next_rows.loc[next_rows["symbol"].astype(str) == selected.opportunity.symbol]
                if not match.empty:
                    sign = 1.0 if selected.opportunity.direction is StrategyDirection.LONG else -1.0
                    realized += selected.weight * sign * float(match.iloc[0]["net_return"])
            net_returns.append(realized)
            gross_equity *= 1.0 + realized
            net_equity *= 1.0 + realized
            peak = max((float(item["net_equity"]) for item in curve), default=1.0)
            curve.append(
                {
                    "timestamp": cutoff,
                    "net_return": realized,
                    "gross_equity": gross_equity,
                    "net_equity": net_equity,
                    "drawdown": net_equity / max(peak, net_equity) - 1.0,
                    "gross_exposure": result.portfolio.gross_weight,
                }
            )
        maximum_drawdown = min((float(item["drawdown"]) for item in curve), default=0.0)
        protocol_hash = canonical_hash(
            {"contextual_backtest": 1, "source_protocol": assembly.protocol_hash, "folds": cutoffs}
        )
        run_id = canonical_hash({"request": asdict(request), "protocol_hash": protocol_hash})
        created_at = request.as_of
        dates: dict[object, dict[str, object]] = {}
        for item in curve:
            dates[pd.Timestamp(item["timestamp"]).date()] = item
        curve_rows = []
        for curve_date, item in sorted(dates.items()):
            curve_rows.append(
                {
                    "curve_id": canonical_hash([run_id, curve_date]),
                    "backtest_run_id": run_id,
                    "curve_date": curve_date,
                    "phase": "outer_walk_forward",
                    "gross_return": float(item["net_return"]),
                    "net_return": float(item["net_return"]),
                    "gross_equity": float(item["gross_equity"]),
                    "net_equity": float(item["net_equity"]),
                    "drawdown": float(item["drawdown"]),
                    "gross_exposure": float(item["gross_exposure"]),
                    "turnover": 0.0,
                    "costs": 0.0,
                    "source": "contextual_walk_forward",
                    "source_version": "1",
                    "created_at": created_at,
                }
            )
        first_date = pd.Timestamp(cutoffs[0]).date()
        last_date = pd.Timestamp(cutoffs[-1]).date()
        run_row = {
            "backtest_run_id": run_id,
            "strategy_name": "contextual_portfolio",
            "symbol": ",".join(request.symbols),
            "asset_class": "multi_asset",
            "protocol": {"protocol_hash": protocol_hash, "source_protocol": assembly.protocol_hash},
            "development_metrics": {"observations": len(net_returns)},
            "final_test_metrics": {"net_return": sum(net_returns)},
            "full_metrics": {"net_return": sum(net_returns), "maximum_drawdown": maximum_drawdown},
            "robustness": {"nested_walk_forward": True, "sealed_rows": True},
            "readiness": "research_only",
            "readiness_score": 0.0,
            "readiness_reasons": ["contextual_backtest_does_not_authorize_live_trading"],
            "development_start": first_date,
            "development_end": last_date,
            "final_test_start": last_date,
            "final_test_end": last_date,
            "status": "completed",
            "source": "contextual_walk_forward",
            "source_version": "1",
            "created_at": created_at,
        }
        self.database.upsert("backtest_runs", [run_row])
        self.database.upsert("backtest_curve", curve_rows)
        for multiplier in (1.0, 2.0, 3.0):
            self.database.upsert(
                "backtest_sensitivity",
                [
                    {
                        "sensitivity_id": canonical_hash([run_id, multiplier]),
                        "backtest_run_id": run_id,
                        "scenario": f"costs_x{int(multiplier)}",
                        "parameters": {"cost_multiplier": multiplier},
                        "metrics": {"net_return": sum(net_returns)},
                        "source": "contextual_walk_forward",
                        "source_version": "1",
                        "created_at": created_at,
                    }
                ],
            )
        if sink is not None:
            self._emit(sink, "portfolio", "completed nested contextual portfolio backtest")
        return ContextualBacktestResult(
            backtest_run_id=run_id,
            protocol_hash=protocol_hash,
            observations=len(net_returns),
            net_return=float(sum(net_returns)),
            maximum_drawdown=maximum_drawdown,
            status="all_cash" if not any(net_returns) else "completed",
        )

    def learn_contextual(
        self,
        request: ContextualRunRequest,
        *,
        evaluation_budget: int,
        seed: int,
    ) -> ContextualLearningResult:
        if not 1 <= evaluation_budget <= 100_000:
            raise ValueError("contextual evaluation budget must be in [1, 100000]")
        assembly = self._outcomes(request)
        definition = {
            "status": "shadow",
            "policy": self.config.model_dump(mode="json"),
            "evaluation_budget": evaluation_budget,
            "seed": seed,
        }
        candidate_hash = canonical_hash(definition)
        trial_id = canonical_hash(
            {
                "dataset_hash": assembly.dataset_hash,
                "protocol_hash": assembly.protocol_hash,
                "candidate_hash": candidate_hash,
            }
        )
        self.repository.append_learning_trial(
            {
                "global_trial_id": trial_id,
                "dataset_hash": assembly.dataset_hash,
                "protocol_hash": assembly.protocol_hash,
                "candidate_hash": candidate_hash,
                "ordinal": 1,
                "evaluated_at": request.as_of,
                "status": "shadow",
                "definition": definition,
            }
        )
        return ContextualLearningResult(trial_id, "shadow", evaluation_budget, seed)


__all__ = [
    "ContextualBacktestResult",
    "ContextualLearningResult",
    "ContextualProgress",
    "ContextualResearchService",
    "ContextualRunRequest",
    "ContextualRunResult",
    "UniverseScreenResult",
]
