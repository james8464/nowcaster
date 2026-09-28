"""Deterministic first-opt-in manifest preparation; never reads market observations."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from src.background_research.contracts import LearningCampaign, LearningCostPolicy, LearningSearchSpace
from src.background_research.registry import _safe_directory, _validate_source
from src.research.round_two_registry import _write_first_manifest, load_round_protocol
from src.strategies.types import canonical_json

# Explicit grammar declarations for the three registered paper-desk families.
# Parameters retain the configured definition plus the source candidate's overrides.
_GRAMMAR = {
    "desk_ema_adx_trend_1m": (("close", "ema_12", "ema_26", "adx_14"), (15.0, 20.0, 25.0)),
    "desk_donchian_breakout_1m": (("close", "donchian_upper", "donchian_lower"), (0.0, 1.0)),
    "desk_vwap_trend_continuation_1m": (("close", "session_vwap"), (0.0, 1.0)),
}


def prepare_background_research(
    *, source_directory: Path, output: Path, campaign_id: str, seed: int, created_at: str
) -> dict:
    source = source_directory.expanduser().resolve()
    protocol_path = (source / "protocol.json").resolve()
    if {"ProspectiveStudies", "live-paper-study"}.intersection((*source.parts, *protocol_path.parts)):
        raise ValueError("protected study cannot be used as a learning source")
    output = output.expanduser().absolute()
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError("manifest path cannot contain symbolic links")
    destination = _safe_directory(output.parent) / output.name
    if source == destination.parent or source in destination.parents or destination in source.parents:
        raise ValueError("manifest must be separate from source")
    protocol = load_round_protocol(source).validated()
    # This existing registry resolves actual supported strategy versions/parameters.
    from src.research.round_two_runtime import _strategy_registry

    registry = _strategy_registry()
    spaces = []
    pairs = set()
    for candidate in protocol.candidates:
        pair = candidate.symbol, candidate.strategy_id
        if pair in pairs or candidate.direction != "long" or candidate.strategy_id not in _GRAMMAR:
            raise ValueError("unsupported or ambiguous source candidate family")
        pairs.add(pair)
        registered = registry.resolve(candidate.strategy_id)
        if registered.spec.version != candidate.strategy_version or protocol.interval not in registered.spec.intervals:
            raise ValueError("unsupported source candidate version or interval")
        parameters = {**registered.spec.parameters, **dict(candidate.parameters)}
        if set(parameters) != set(registered.spec.parameters):
            raise ValueError("unsupported source candidate parameters")
        indicators, thresholds = _GRAMMAR[candidate.strategy_id]
        spaces.append(
            LearningSearchSpace(
                symbol=candidate.symbol,
                strategy_id=candidate.strategy_id,
                base_parameters=parameters,
                parameter_grid={},
                seed_rules=(),
                indicators=indicators,
                thresholds=thresholds,
                maximum_lag=2,
                max_depth=4,
                max_nodes=15,
            )
        )
    campaign = LearningCampaign(
        campaign_id=campaign_id,
        source_directory=source,
        source_protocol_hash=protocol.identity_hash,
        code_hash="0" * 64,
        symbols=protocol.symbols,
        search_spaces=tuple(spaces),
        cost_policy=LearningCostPolicy.from_protocol(protocol),
        schedule=protocol.schedule,
        seed=seed,
        max_attempts_per_batch=100,
        max_batches_per_asset_day=1,
        created_at=datetime.fromisoformat(created_at.replace("Z", "+00:00")),
    )
    _validate_source(campaign, protocol)
    payload = campaign.model_dump(mode="json", exclude={"code_hash"})
    encoded = (canonical_json(payload) + "\n").encode()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.stat().st_nlink != 1:
        raise ValueError("manifest cannot be hard linked")
    if not _write_first_manifest(destination, encoded) and (
        destination.is_symlink() or destination.read_bytes() != encoded
    ):
        raise ValueError("existing manifest is immutable; preparation differs")
    return {
        "event": "prepared",
        "stage": "background_research",
        "schema_version": 1,
        "source_protocol_hash": protocol.identity_hash,
        "campaign_id": campaign_id,
        "manifest": str(destination),
    }
