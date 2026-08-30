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
from src.contextual.authentication import evidence_mirrors_match
from src.contextual.backtest import realize_weighted_outcomes
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
from src.contextual.market import observed_execution_inputs
from src.contextual.portfolio import (
    PortfolioSelection,
    ResearchOpportunity,
    select_portfolio_opportunities,
)
from src.contextual.regimes import causal_regime_features, fit_regime_model, predict_regime_posteriors
from src.contextual.repository import ContextualRepository
from src.contextual.types import ContextLevel, EligibilityState, StrategyContextKey, StrategyDirection
from src.database.engine import Database
from src.deep_research.contracts import ChampionChallengerTransition
from src.ingestion.bars import INTERVAL_DURATION
from src.learning.search import (
    ContextualCandidate,
    ContextualCandidateEvaluation,
    ContextualLearningExperiment,
    ContextualSearchSpace,
    evaluate_contextual_candidate,
    generate_contextual_candidates,
)
from src.live_monitor.types import MarketDepth, MarketQuote, MarketStatusEvent
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
    trial_count: int = 0
    candidate_hash: str | None = None
    fitness: float | None = None
    shadow_cohort_hash: str | None = None


@dataclass(frozen=True, slots=True)
class _OutcomeAssembly:
    frame: pd.DataFrame
    dataset_hash: str
    protocol_hash: str
    source_datasets: Mapping[str, str]
    source_cohorts: Mapping[str, Mapping[str, object]]


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
        maximum_age_seconds: float,
    ) -> tuple[str, MarketQuote | MarketDepth | MarketStatusEvent] | None:
        frame = self.database.frame(
            "select * from live_market_events where provider = :provider and feed = :feed "
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
        row = frame.iloc[0]
        payload = row["payload"]
        if canonical_hash(payload) != str(row["payload_hash"]):
            return None
        model = {"quote": MarketQuote, "depth": MarketDepth, "status": MarketStatusEvent}[event_type]
        try:
            event = model.model_validate(payload)
        except (ValueError, TypeError):
            return None
        if (
            (event.provider, event.feed, event.symbol) != (request.provider, request.feed, symbol)
            or event.event_id != str(row["source_event_id"])
            or canonical_hash((str(row["session_id"]), event.event_id)) != str(row["event_id"])
            or any(
                pd.Timestamp(row[name]) != pd.Timestamp(getattr(event, name))
                for name in ("provider_time", "received_at", "processed_at")
            )
            or event.processed_at is None
            or event.processed_at > request.as_of
            or not 0 <= (request.as_of - event.provider_time).total_seconds() <= maximum_age_seconds
        ):
            return None
        return str(row["event_id"]), event

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
            research_size_notional=policy.research_probe_notional,
        )
        quote = self._latest_live_payload(request, instrument.symbol, "quote", policy.maximum_data_age_seconds)
        depth = self._latest_live_payload(request, instrument.symbol, "depth", policy.maximum_data_age_seconds)
        rules = self._latest_live_payload(request, instrument.symbol, "status", 24 * 60 * 60)
        if quote is not None and depth is not None and rules is not None:
            try:
                inputs = observed_execution_inputs(
                    inputs,
                    quote[1],
                    depth[1],
                    rules[1],
                    probe_notional=policy.research_probe_notional,
                )
            except (ValueError, TypeError, KeyError, ArithmeticError):
                pass  # Preserve missing-evidence gates; malformed books never supply an optimistic substitute.
            else:
                inputs = type(inputs).model_validate(
                    {
                        **inputs.model_dump(),
                        "source_event_watermark": canonical_hash(
                            [inputs.source_event_watermark, quote[0], depth[0], rules[0]]
                        ),
                    }
                )
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
        persist: bool = True,
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
                if persist:
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
            if persist:
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
            "select dataset_hash, strategy_id, strategy_version, run_timestamp, metrics from strategy_runs "
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
            effective = pd.Timestamp(effective_at)
            if pd.isna(effective) or effective.tzinfo is None or effective > request.as_of:
                continue
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
                or set(group["strategy_id"].astype(str)) != set(members)
                or any(
                    str(row.strategy_id) not in self._specs
                    or str(row.strategy_version) != self._specs[str(row.strategy_id)].deterministic_version
                    or pd.Timestamp(row.run_timestamp) > effective
                    for row in group.itertuples(index=False)
                )
                or any(
                    any(
                        item.get(key) != first.get(key)
                        for key in (
                            "cohort_members",
                            "contextual_outcome_count",
                            "contextual_outcome_index_hash",
                            "contextual_protocol_hash",
                        )
                    )
                    for item in metrics
                )
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
            if (
                len(index) == count
                and canonical_hash(index) == index_hash
                and not selected.empty
                and (selected["outcome_available_at"] <= effective).all()
            ):
                selected = selected.copy()
                selected.attrs["source_cohort"] = {
                    "cohort_id": str(_cohort_id),
                    "effective_at": effective.isoformat(),
                    "member_versions": {
                        str(item["strategy_id"]): str(item["strategy_version"]) for item in first["cohort_members"]
                    },
                    "outcome_index_hash": index_hash,
                    "protocol_hash": protocol,
                }
                valid.append((effective.to_pydatetime(), selected))
        if not valid:
            raise ValueError(f"{symbol} has strategy runs but no complete contextual outcome cohort")
        return max(valid, key=lambda item: item[0])[1]

    def _outcomes(self, request: ContextualRunRequest) -> _OutcomeAssembly:
        frame = self.database.frame(
            "select * from contextual_outcomes where provider = :provider and feed = :feed "
            "and interval = :interval and mode = :mode",
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
        # Authenticate the complete published source cohort before taking any historical prefix.
        # Publication availability and outcome availability are separate clocks.
        selected_by_symbol: dict[str, pd.DataFrame] = {}
        source_datasets: dict[str, str] = {}
        source_cohorts: dict[str, Mapping[str, object]] = {}
        for symbol in request.symbols:
            candidate = frame.loc[frame["symbol"].astype(str) == symbol].copy()
            if candidate.empty:
                raise ValueError(f"no contextual outcome cohort is available for {symbol}")
            complete = self._complete_run_context(symbol, candidate, request)
            if complete is None:
                candidate = candidate.loc[candidate["outcome_available_at"] <= request.as_of]
                group_scores = []
                for key, group in candidate.groupby(
                    ["dataset_hash", "protocol_hash", "code_hash", "config_hash"], sort=True
                ):
                    group_scores.append((group["created_at"].max(), group["outcome_available_at"].max(), key, group))
                if not group_scores:
                    raise ValueError(f"no available contextual outcome cohort for {symbol}")
                complete = max(group_scores, key=lambda item: (item[0], item[1], item[2]))[3]
            selected_by_symbol[symbol] = complete
            source_datasets[symbol] = str(complete.iloc[0]["dataset_hash"])
            source_cohorts[symbol] = dict(complete.attrs.get("source_cohort", {}))
        frame = pd.concat(tuple(selected_by_symbol.values()), ignore_index=True)
        for index, row in frame.iterrows():
            payload = row["evidence"]
            try:
                if not isinstance(payload, dict):
                    raise ValueError("missing outcome payload")
                authenticated = self.repository.row_for_outcome(payload)
                for name, expected in authenticated.items():
                    if name in {"source", "source_version", "created_at", "evidence"}:
                        continue
                    observed = row[name]
                    if isinstance(expected, datetime):
                        matches = pd.Timestamp(expected) == pd.Timestamp(observed)
                    elif isinstance(expected, float):
                        matches = math.isfinite(float(observed)) and math.isclose(
                            expected, float(observed), rel_tol=1e-6, abs_tol=1e-9
                        )
                    else:
                        matches = expected == observed
                    if not matches:
                        raise ValueError(f"outcome mirror mismatch: {name}")
                spec = self._specs.get(str(payload["strategy_id"]))
                if spec is None or payload.get("strategy_version") != spec.deterministic_version:
                    raise ValueError("outcome strategy version does not match the configured definition")
            except (ValueError, TypeError, KeyError) as error:
                raise ValueError("contextual outcome authentication failed") from error
            # Use the authenticated doubles, not rounded SQL FLOAT mirrors, for reproducible mathematics.
            for name in ("gross_return", "modeled_cost", "net_return"):
                frame.at[index, name] = authenticated[name]

        selected = frame.loc[frame["outcome_available_at"] <= request.as_of].copy()
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
        selected["eligibility_quality"] = selected["evidence"].map(
            lambda value: (
                float(value.get("eligibility_evidence", {}).get("quality_score", 0.0))
                if isinstance(value, dict) and isinstance(value.get("eligibility_evidence"), dict)
                else 0.0
            )
        )
        selected["holding_horizon_bars"] = selected["evidence"].map(lambda value: value.get("holding_horizon_bars", 1))
        known = set(self._specs)
        selected = selected.loc[selected["strategy_id"].astype(str).isin(known)].copy()
        if selected.empty:
            raise ValueError("contextual outcomes do not reference enabled local strategies")
        return _OutcomeAssembly(
            frame=selected,
            dataset_hash=dataset_hash,
            protocol_hash=next(iter(protocols)),
            source_datasets=MappingProxyType(source_datasets),
            source_cohorts=MappingProxyType(source_cohorts),
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

    def _previous_weights(self, context_hash: str, strategy_ids: Sequence[str], as_of: datetime) -> dict[str, float]:
        frame = self.database.frame(
            "select * from contextual_weights where context_hash = :context_hash "
            "and effective_at < :as_of order by effective_at desc",
            {"context_hash": context_hash, "as_of": as_of},
        )
        if frame.empty:
            return {item: 0.0 for item in strategy_ids}
        latest = frame.iloc[0]["allocation_id"]
        selected = frame.loc[frame["allocation_id"] == latest]
        observed: dict[str, float] = {}
        for row in selected.itertuples(index=False):
            if (
                not evidence_mirrors_match(row._asdict(), weight_record=True)
                or row.evidence != selected.iloc[0]["evidence"]
                or canonical_hash(
                    {"allocation_id": row.evidence["allocation"]["allocation_id"], "context_hash": context_hash}
                )
                != str(row.allocation_id)
                or canonical_hash({"allocation_id": str(row.allocation_id), "strategy_id": str(row.strategy_id)})
                != str(row.contextual_weight_id)
            ):
                raise ValueError("prior contextual weight authentication failed")
            observed[str(row.strategy_id)] = float(row.evidence["allocation"]["weights"][str(row.strategy_id)])
        if len(observed) != len(selected) or set(observed) != set(
            selected.iloc[0]["evidence"]["allocation"]["weights"]
        ):
            raise ValueError("prior contextual allocation is incomplete")
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
        symbol_rows = assembly.frame.loc[assembly.frame["symbol"].astype(str) == instrument.symbol]
        strategy_versions: dict[str, str] = {}
        for strategy_id, rows in symbol_rows.groupby("strategy_id", sort=True):
            versions = {
                str(payload["strategy_version"])
                for payload in rows["evidence"]
                if isinstance(payload, dict) and payload.get("strategy_version")
            }
            if len(versions) == 1:
                strategy_versions[str(strategy_id)] = next(iter(versions))
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
            "source_dataset_hash": assembly.source_datasets[instrument.symbol],
            "source_cohort": dict(assembly.source_cohorts[instrument.symbol]),
            "protocol_hash": assembly.protocol_hash,
            "provider": request.provider,
            "feed": request.feed,
            "venue": instrument.venue,
            "product": instrument.product,
            "asset_class": instrument.asset_class,
            "profile": instrument.profile.value,
            "symbol": instrument.symbol,
            "interval": request.interval.value,
            "direction": direction.value,
            "mode": request.mode.value,
            "strategy_versions": strategy_versions,
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

    def _prior_drift(self, context_hash: str, as_of: datetime) -> tuple[str, str | None]:
        frame = self.database.frame(
            "select * from contextual_drift_events where context_hash = :context_hash "
            "and effective_at <= :as_of order by effective_at desc, created_at desc limit 128",
            {"context_hash": context_hash, "as_of": as_of},
        )
        if frame.empty:
            return "stable", None
        candidates = []
        severity = {"stable": 0, "warning": 1, "unavailable": 2, "confirmed": 3}
        for row in frame.to_dict("records"):
            if not evidence_mirrors_match(row):
                return "unavailable", None
            payload = row["evidence"]
            if pd.Timestamp(row["effective_at"]) == as_of and payload.get("reason") in {
                "authenticated_context_baseline",
                "prior_drift_quarantine_preserved",
            }:
                continue  # Do not consume this same assessment's own idempotent baseline.
            status = str(row["status"])
            candidates.append(
                (severity.get(status, 2), pd.Timestamp(row["effective_at"]), str(row["content_hash"]), status)
            )
        if not candidates:
            return "stable", None
        _, _, content_hash, status = max(candidates)
        return status, content_hash

    def evaluate_contexts(
        self,
        request: ContextualRunRequest,
        sink: EventSink | None = None,
        *,
        _replay_assembly: _OutcomeAssembly | None = None,
        _replay_previous: Mapping[str, ContextualAllocation] | None = None,
    ) -> ContextualRunResult:
        self._validate_request(request)
        persist = _replay_assembly is None
        assembly = (
            self._outcomes(request)
            if persist
            else replace(
                _replay_assembly,
                frame=_replay_assembly.frame.loc[
                    (_replay_assembly.frame["outcome_available_at"] <= request.as_of)
                    & (_replay_assembly.frame["decision_timestamp"] < request.as_of)
                ].copy(),
            )
        )
        screen = self._screen(
            request,
            sink,
            identity=(assembly.dataset_hash, assembly.protocol_hash),
            persist=persist,
        )
        hierarchy = build_hierarchical_estimates(
            assembly.frame,
            request.as_of,
            self.config.hierarchy_prior_strengths,
        )
        if persist:
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
                context["eligibility_id"] = eligibility[(symbol, direction)].evidence_id
                context["eligibility_policy_hash"] = eligibility[(symbol, direction)].policy_hash
                context["hierarchy_hash"] = hierarchy.evidence_hash
                context["blended_estimates"] = {
                    strategy_id: asdict(estimate) for strategy_id, estimate in blended.items()
                }
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
                    (
                        self._previous_weights(str(context["context_hash"]), strategy_ids, request.as_of)
                        if persist
                        else (
                            dict(_replay_previous[f"{symbol}:{direction.value}"].weights)
                            if _replay_previous and f"{symbol}:{direction.value}" in _replay_previous
                            else {}
                        )
                    ),
                    families,
                    self.config.allocation,
                    request.as_of,
                    applicable=applicable,
                )
                key = f"{symbol}:{direction.value}"
                allocations[key] = allocation
                blended_by_context[key] = MappingProxyType(blended)
                context_records[key] = context
                if persist:
                    self.repository.append_covariance(allocation.covariance, context)
                    self.repository.append_allocation(allocation, context)
        if not allocations:
            raise ValueError("no configured asset-direction context has authenticated strategy outcomes")
        self._emit(sink, "covariance", f"validated {len(allocations)} strategy covariance contexts")
        self._emit(sink, "allocation", f"allocated {len(allocations)} strategy contexts")

        opportunities: list[ResearchOpportunity] = []
        prior_drift = {
            key: self._prior_drift(str(context["context_hash"]), request.as_of)
            for key, context in context_records.items()
        }
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
                    eligible=(
                        evidence.state is EligibilityState.ELIGIBLE
                        and allocation.status == "allocated"
                        and prior_drift[key][0] == "stable"
                    ),
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
        ranked_opportunities = sorted(
            opportunities,
            key=lambda item: (
                -item.lower_net_edge,
                -item.liquidity_quality,
                -item.probability_lower,
                item.decision_time,
                item.decision_hash,
            ),
        )
        portfolio_ranks = {item.decision_hash: rank for rank, item in enumerate(ranked_opportunities, start=1)}
        for opportunity in opportunities:
            if not persist:
                continue
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
                    "portfolio_rank": portfolio_ranks[opportunity.decision_hash],
                    "research_size_ceiling": selected.size_evidence.ceiling if selected is not None else 0.0,
                    "size_evidence": asdict(selected.size_evidence) if selected is not None else None,
                    "portfolio_covariance_hash": portfolio.covariance_hash,
                    "portfolio_cash_weight": portfolio.cash_weight,
                    "exclusion_reasons": reasons or (() if selected is not None else ("not_selected",)),
                    "opportunity": asdict(opportunity),
                }
            )
        for key, allocation in allocations.items():
            if not persist:
                continue
            context = context_records[key]
            self.repository.append_drift_event(
                {
                    "context_hash": context["context_hash"],
                    "effective_at": request.as_of,
                    "status": prior_drift[key][0],
                    "reason": "authenticated_context_baseline"
                    if prior_drift[key][0] == "stable"
                    else "prior_drift_quarantine_preserved",
                    "previous_drift_hash": prior_drift[key][1],
                    "dataset_hash": assembly.dataset_hash,
                    "source_dataset_hash": context["source_dataset_hash"],
                    "protocol_hash": assembly.protocol_hash,
                    "allocation_id": allocation.allocation_id,
                    "covariance_hash": allocation.covariance.evidence_hash,
                    "selection_id": portfolio.selection_id,
                    "screen_hash": screen.evidence_hash,
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
        # Pin and authenticate the full source once. Every fit below receives only
        # outcomes resolved by that decision; replay never publishes live receipts.
        self._validate_request(request)
        assembly = self._outcomes(request)
        timestamps = tuple(sorted(assembly.frame["decision_timestamp"].unique()))
        if len(timestamps) < 60:
            raise ValueError("contextual portfolio backtest requires at least 60 resolved timestamps")
        cutoffs = tuple(pd.Timestamp(value).to_pydatetime() for value in timestamps[40:])
        final_start = cutoffs[int(len(cutoffs) * 0.80)]
        policy = self.settings.deep_research
        fee = max(
            policy.crypto_fee_bps
            if self._instrument(symbol, request).asset_class == "crypto"
            else policy.equity_fee_bps
            for symbol in request.symbols
        )
        rebalance_rate = (fee + policy.half_spread_bps + policy.slippage_bps) / 10_000
        previous_positions = {}
        previous_allocations = {}
        gross_equity = net_equity = peak = 1.0
        periods = []
        for index, cutoff in enumerate(cutoffs):
            result = self.evaluate_contexts(
                replace(request, as_of=cutoff),
                _replay_assembly=assembly,
                _replay_previous=previous_allocations,
            )
            positions = {}
            for selected in result.portfolio.selected:
                symbol, direction = selected.opportunity.symbol, selected.opportunity.direction.value
                allocation = result.allocations[f"{symbol}:{direction}"]
                for strategy_id, weight in allocation.weights.items():
                    if weight > 0:
                        positions[(symbol, direction, strategy_id)] = selected.weight * weight
            realized = realize_weighted_outcomes(
                positions,
                assembly.frame,
                cutoff,
                INTERVAL_DURATION[request.interval],
                previous_positions,
                rebalance_rate,
            )
            # Liquidate at the end, including the cost of returning to cash.
            closing_turnover = sum(positions.values()) if index == len(cutoffs) - 1 else 0.0
            costs = realized.costs + closing_turnover * rebalance_rate
            net_return = realized.gross_return - costs
            gross_equity *= 1 + realized.gross_return
            net_equity *= 1 + net_return
            peak = max(peak, net_equity)
            periods.append(
                {
                    "timestamp": cutoff.isoformat(),
                    "phase": "retrospective_holdout" if cutoff >= final_start else "walk_forward_development",
                    "gross_return": realized.gross_return,
                    "net_return": net_return,
                    "costs": costs,
                    "source_costs": realized.source_costs,
                    "turnover": realized.turnover + closing_turnover,
                    "gross_exposure": realized.gross_exposure,
                    "gross_equity": gross_equity,
                    "net_equity": net_equity,
                    "drawdown": net_equity / peak - 1,
                    "decision_hash": result.evidence_hash,
                }
            )
            previous_positions = positions
            previous_allocations = result.allocations
            if sink is not None:
                sink(
                    ContextualProgress(
                        "portfolio",
                        (index + 1) / len(cutoffs),
                        f"replayed {index + 1} of {len(cutoffs)} chronological decisions",
                    )
                )

        def metrics(rows, multiplier=1.0):
            returns = [float(row["gross_return"]) - multiplier * float(row["costs"]) for row in rows]
            if any(value <= -1 for value in returns):
                return {"observations": len(rows), "net_return": -1.0, "insolvent": True}
            return {"observations": len(rows), "net_return": float(np.prod(1 + np.asarray(returns)) - 1)}

        protocol = {
            "contextual_backtest": 2,
            "source_protocol": assembly.protocol_hash,
            "source_datasets": dict(assembly.source_datasets),
            "policy_hash": canonical_hash(self.config.model_dump(mode="json")),
            "source_index_hash": canonical_hash(
                sorted(
                    assembly.frame[["outcome_id", "content_hash"]].to_dict("records"),
                    key=lambda row: row["outcome_id"],
                )
            ),
            "warmup_timestamps": 40,
            "final_start": final_start.isoformat(),
            "rebalance_cost_rate": rebalance_rate,
            "holding_horizon_bars": 1,
            "directional_returns_already_signed": True,
            "missing_execution_policy": "reject_replay",
            "period_index_hash": canonical_hash(periods),
        }
        protocol_hash = canonical_hash(protocol)
        run_id = canonical_hash({"request": asdict(request), "protocol_hash": protocol_hash})
        maximum_drawdown = min(float(row["drawdown"]) for row in periods)
        common = {"source": "contextual_walk_forward", "source_version": "2", "created_at": request.as_of}
        development = [row for row in periods if row["phase"] == "walk_forward_development"]
        final = [row for row in periods if row["phase"] == "retrospective_holdout"]
        self.database.upsert(
            "backtest_runs",
            [
                {
                    "backtest_run_id": run_id,
                    "strategy_name": "contextual_portfolio",
                    "symbol": ",".join(request.symbols),
                    "asset_class": "multi_asset",
                    "protocol": {**protocol, "protocol_hash": protocol_hash},
                    "development_metrics": metrics(development),
                    "final_test_metrics": {**metrics(final), "independent_sealed_test": False},
                    "full_metrics": {**metrics(periods), "maximum_drawdown": maximum_drawdown},
                    "robustness": {
                        "nested_walk_forward": False,
                        "walk_forward_refit": True,
                        "sealed_rows": False,
                        "replay_only": True,
                        "policy_frozen": True,
                        "all_resolved_decisions_after_warmup": True,
                        "historical_executability_required": True,
                        "periods": periods,
                    },
                    "readiness": "research_only",
                    "readiness_score": 0.0,
                    "readiness_reasons": [
                        "contextual_backtest_does_not_authorize_live_trading",
                        "retrospective_holdout_is_not_independent_forward_evidence",
                    ],
                    "development_start": cutoffs[0].date(),
                    "development_end": pd.Timestamp(development[-1]["timestamp"]).date(),
                    "final_test_start": final_start.date(),
                    "final_test_end": cutoffs[-1].date(),
                    "status": "completed",
                    **common,
                }
            ],
        )
        dates = {}
        for row in periods:
            dates.setdefault(pd.Timestamp(row["timestamp"]).date(), []).append(row)
        curve_rows = []
        for date, rows in sorted(dates.items()):
            curve_rows.append(
                {
                    "curve_id": canonical_hash([run_id, date]),
                    "backtest_run_id": run_id,
                    "curve_date": date,
                    "phase": rows[-1]["phase"],
                    "gross_return": float(np.prod([1 + float(row["gross_return"]) for row in rows]) - 1),
                    "net_return": metrics(rows)["net_return"],
                    "gross_equity": rows[-1]["gross_equity"],
                    "net_equity": rows[-1]["net_equity"],
                    "drawdown": min(float(row["drawdown"]) for row in rows),
                    "gross_exposure": float(np.mean([row["gross_exposure"] for row in rows])),
                    "turnover": sum(float(row["turnover"]) for row in rows),
                    "costs": sum(float(row["costs"]) for row in rows),
                    **common,
                }
            )
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
                        "metrics": metrics(periods, multiplier),
                        **common,
                    }
                ],
            )
        return ContextualBacktestResult(
            backtest_run_id=run_id,
            protocol_hash=protocol_hash,
            observations=len(periods),
            net_return=metrics(periods)["net_return"],
            maximum_drawdown=maximum_drawdown,
            status="completed" if any(row["gross_exposure"] > 0 for row in periods) else "all_cash",
        )

    def learn_contextual(
        self,
        request: ContextualRunRequest,
        *,
        evaluation_budget: int,
        seed: int,
        sink: EventSink | None = None,
    ) -> ContextualLearningResult:
        if not 1 <= evaluation_budget <= 100_000:
            raise ValueError("contextual evaluation budget must be in [1, 100000]")
        self._validate_request(request)
        assembly = self._outcomes(request)
        timestamps = tuple(sorted(assembly.frame["decision_timestamp"].unique()))
        if len(timestamps) < 90:
            raise ValueError("contextual learning requires at least 90 resolved development timestamps")
        sealed_start = pd.Timestamp(timestamps[int(len(timestamps) * 0.80)]).to_pydatetime()
        development = assembly.frame.loc[
            (assembly.frame["decision_timestamp"] < sealed_start)
            & (assembly.frame["outcome_available_at"] < sealed_start)
        ].copy()
        development_as_of = development["outcome_available_at"].max().to_pydatetime()
        baseline = ContextualCandidate(
            global_prior_strength=self.config.hierarchy_prior_strengths[ContextLevel.GLOBAL],
            asset_class_prior_strength=self.config.hierarchy_prior_strengths[ContextLevel.ASSET_CLASS],
            profile_prior_strength=self.config.hierarchy_prior_strengths[ContextLevel.PROFILE],
            asset_prior_strength=self.config.hierarchy_prior_strengths[ContextLevel.ASSET],
            asset_regime_prior_strength=self.config.hierarchy_prior_strengths[ContextLevel.ASSET_REGIME],
            risk_penalty=self.config.allocation.risk_penalty,
            turnover_penalty=self.config.allocation.turnover_penalty,
            prior_penalty=self.config.allocation.prior_penalty,
            maximum_correlation=self.config.portfolio.maximum_correlation,
            kelly_fraction=self.config.portfolio.kelly_fraction,
        )
        space = ContextualSearchSpace.conservative(baseline)
        candidates = generate_contextual_candidates(space, seed=seed, budget=evaluation_budget)
        protocol_hash = canonical_hash(
            {
                "contextual_search_version": 2,
                "dataset_hash": assembly.dataset_hash,
                "source_protocol_hash": assembly.protocol_hash,
                "source_datasets": dict(assembly.source_datasets),
                "search_space": dict(space.grids),
                "baseline": baseline.definition,
                "sealed_final_start": sealed_start,
                "development_data_through": development_as_of,
                "seed": seed,
                "accounting": "shared_cash_closed_execution_outcomes_v2",
                "rebalance_cost_rate": 0.0017,
            }
        )
        experiment = ContextualLearningExperiment(
            dataset_hash=assembly.dataset_hash,
            protocol_hash=protocol_hash,
            as_of=development_as_of,
            sealed_final_start=sealed_start,
            outer_validation_blocks=3,
            minimum_train_timestamps=40,
            minimum_validation_timestamps=10,
        )
        identities: dict[str, str] = {}
        for ordinal, candidate in enumerate(candidates, start=1):
            identity = candidate.global_trial_id(assembly.dataset_hash, protocol_hash, ordinal)
            identities[candidate.candidate_hash] = identity
            definition = {
                "candidate": candidate.definition,
                "candidate_hash": candidate.candidate_hash,
                "source_protocol_hash": assembly.protocol_hash,
                "source_datasets": dict(assembly.source_datasets),
                "development_data_through": development_as_of.isoformat(),
                "sealed_final_start": sealed_start.isoformat(),
                "state": "shadow",
            }
            existing = self.database.frame(
                "select content_hash, evidence, definition from contextual_learning_trials "
                "where global_trial_id = :identity",
                {"identity": identity},
            )
            if not existing.empty:
                row = existing.iloc[0]
                if canonical_hash(row["evidence"]) != str(row["content_hash"]) or row["definition"] != definition:
                    raise ValueError("persisted contextual trial conflicts with its immutable search definition")
                self.repository.append_learning_trial_event(
                    {
                        "global_trial_id": identity,
                        "status": "duplicate",
                        "rung": 0,
                        "evaluated_at": datetime.now(UTC),
                        "candidate_hash": candidate.candidate_hash,
                    }
                )
                continue
            self.repository.append_learning_trial(
                {
                    "global_trial_id": identity,
                    "dataset_hash": assembly.dataset_hash,
                    "protocol_hash": protocol_hash,
                    "candidate_hash": candidate.candidate_hash,
                    "ordinal": ordinal,
                    "evaluated_at": datetime.now(UTC),
                    "status": "generated",
                    "definition": definition,
                }
            )
        self._emit(sink, "hierarchy", f"reserved {len(candidates)} globally identified contextual trials")

        survivors = list(candidates)
        results: dict[str, ContextualCandidateEvaluation] = {}
        for rung in (1, 2, 3):
            successful: list[ContextualCandidate] = []
            for candidate in survivors:
                identity = identities[candidate.candidate_hash]
                try:
                    result = evaluate_contextual_candidate(
                        candidate,
                        development,
                        replace(experiment, outer_validation_blocks=rung),
                    )
                except (KeyboardInterrupt, SystemExit):
                    self.repository.append_learning_trial_event(
                        {
                            "global_trial_id": identity,
                            "status": "interrupted",
                            "rung": rung,
                            "evaluated_at": datetime.now(UTC),
                            "candidate_hash": candidate.candidate_hash,
                        }
                    )
                    raise
                except Exception as error:
                    self.repository.append_learning_trial_event(
                        {
                            "global_trial_id": identity,
                            "status": "failed",
                            "rung": rung,
                            "evaluated_at": datetime.now(UTC),
                            "candidate_hash": candidate.candidate_hash,
                            "error_summary": f"{type(error).__name__}: {error}"[:1_000],
                        }
                    )
                    continue
                results[candidate.candidate_hash] = result
                successful.append(candidate)
                self.repository.append_learning_trial_event(
                    {
                        "global_trial_id": identity,
                        "status": "succeeded",
                        "rung": rung,
                        "evaluated_at": datetime.now(UTC),
                        "candidate_hash": candidate.candidate_hash,
                        "fitness": result.fitness,
                        "result": asdict(result),
                        "state": "shadow",
                    }
                )
            ranked = sorted(
                successful,
                key=lambda item: (-results[item.candidate_hash].fitness, item.candidate_hash),
            )
            if rung < 3:
                keep = max((len(ranked) + 1) // 2, 1)
                for candidate in ranked[keep:]:
                    self.repository.append_learning_trial_event(
                        {
                            "global_trial_id": identities[candidate.candidate_hash],
                            "status": "halved",
                            "rung": rung,
                            "evaluated_at": datetime.now(UTC),
                            "candidate_hash": candidate.candidate_hash,
                            "fitness": results[candidate.candidate_hash].fitness,
                        }
                    )
                survivors = ranked[:keep]
            else:
                survivors = ranked
            self._emit(sink, "allocation", f"completed contextual search rung {rung}; {len(survivors)} survivors")
            if not survivors:
                break
        if not survivors:
            raise ValueError(
                "no contextual candidate completed chronological validation; attempted trials were retained"
            )
        champion = survivors[0]
        best = results[champion.candidate_hash]
        transition = ChampionChallengerTransition.start_shadow(
            challenger_hash=champion.candidate_hash,
            incumbent_hash=None,
            protocol_hash=protocol_hash,
            transitioned_at=datetime.now(UTC),
        )
        champion_identity = identities[champion.candidate_hash]
        self.repository.append_learning_trial_event(
            {
                "global_trial_id": champion_identity,
                "status": "shadow",
                "rung": 3,
                "evaluated_at": transition.transitioned_at,
                "candidate_hash": champion.candidate_hash,
                "fitness": best.fitness,
                "result": asdict(best),
                "transition": transition.as_record(),
            }
        )
        self._emit(sink, "portfolio", "contextual champion remains shadow; fresh forward evidence is required")
        return ContextualLearningResult(
            global_trial_id=champion_identity,
            status="shadow",
            evaluation_budget=evaluation_budget,
            seed=seed,
            trial_count=len(candidates),
            candidate_hash=champion.candidate_hash,
            fitness=best.fitness,
            shadow_cohort_hash=transition.shadow_cohort_hash,
        )


__all__ = [
    "ContextualBacktestResult",
    "ContextualLearningResult",
    "ContextualProgress",
    "ContextualResearchService",
    "ContextualRunRequest",
    "ContextualRunResult",
    "UniverseScreenResult",
]
