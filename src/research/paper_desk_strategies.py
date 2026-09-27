"""Explicit 1m hypotheses; no transfer of validation from other bar intervals."""

from src.strategies.library import STRATEGY_GENERATORS
from src.strategies.types import StrategySpec

DESK_STRATEGIES = ("ema_adx_trend", "donchian_breakout", "vwap_trend_continuation")


def generate_desk_signal(spec, bars, context):
    sources = {f"desk_{name}_1m": name for name in DESK_STRATEGIES}
    if spec.strategy_id not in sources or spec.version != "1.0.0" or tuple(spec.intervals) != ("1m",):
        raise ValueError("unknown paper desk definition")
    base_id = sources[spec.strategy_id]
    original = StrategySpec.model_validate({**spec.model_dump(), "strategy_id": base_id})
    return STRATEGY_GENERATORS[base_id](original, bars, context)


def register_desk_strategies(registry):
    for name in DESK_STRATEGIES:
        original = registry.resolve(name)
        spec = StrategySpec.model_validate(
            {**original.spec.model_dump(), "strategy_id": f"desk_{name}_1m", "intervals": ("1m",)}
        )
        registry.register(spec, generate_desk_signal, original.metadata)
