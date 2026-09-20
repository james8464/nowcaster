"""Credential-free orchestration and bounded reports for Research Round 2."""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import yaml

from src.config.settings import StrategiesConfig
from src.research.round_two_contracts import ResearchRoundProtocol, RoundObservation, RoundReport, RoundStatus
from src.research.round_two_quality import append_observations, load_observations, summarize_quality
from src.research.round_two_registry import load_round_protocol, register_round
from src.research.round_two_walkforward import CandidateResult, evaluate_round, validate_retained_candidate_results
from src.strategies.library import build_strategy_registry

SUMMARY_FILE = "research-round-2-summary.json"
_ACTION_SHAPED_KEYS = frozenset({"order", "notification", "alert", "lifecycle", "qualified", "position"})
_WIRE_ROOT_KEYS = frozenset(
    {"roundId", "protocolHash", "status", "paperOnly", "qualificationStatus", "reasons", "candidates"}
)


class PremiumProviderAdapter(Protocol):
    """A future, explicitly supplied provider boundary; it has no credentials here."""

    provider_identity: str

    def observations(self, symbols: tuple[str, ...]) -> tuple[RoundObservation, ...]: ...


class UnconfiguredPremiumProviderAdapter:
    """Fail closed until another round supplies a configured provider implementation."""

    provider_identity = "unconfigured"

    def observations(self, symbols: tuple[str, ...]) -> tuple[RoundObservation, ...]:
        del symbols
        raise RuntimeError("premium provider is not configured")


def parse_utc(value: str) -> datetime:
    """Parse a command-line timestamp only when it explicitly denotes UTC."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise ValueError("starts_at must be an explicit UTC timestamp")
    return parsed.astimezone(UTC)


def register_default_round(directory: Path, starts_at: datetime, *, round_id: str | None = None) -> Path:
    """Create exactly one default Binance-spot research protocol at ``directory``."""
    path = Path(directory)
    protocol = ResearchRoundProtocol.default(round_id=round_id or path.name or "research-round-2", starts_at=starts_at)
    return register_round(protocol, path)


def _contains_action_shape(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if any(token in normalized for token in _ACTION_SHAPED_KEYS):
                return True
            if _contains_action_shape(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_action_shape(item) for item in value)
    return False


def _load_input_rows(path: Path) -> tuple[dict[str, Any], ...]:
    """Read a local finalized-observation fixture without accepting action payloads."""
    content = Path(path).read_text(encoding="utf-8")
    if content.lstrip().startswith("["):
        decoded = json.loads(content)
    else:
        decoded = [json.loads(line) for line in content.splitlines() if line]
    if not isinstance(decoded, list) or any(not isinstance(row, dict) for row in decoded):
        raise ValueError("input must be a JSON array or JSONL object sequence")
    if _contains_action_shape(decoded):
        raise ValueError("action-shaped input is not accepted by paper-only research")
    return tuple(decoded)


def ingest_file(directory: Path, input_path: Path) -> int:
    """Append only validated finalized observations to this separate round."""
    protocol = load_round_protocol(directory)
    rows = _load_input_rows(input_path)
    observations = tuple(RoundObservation.model_validate(row) for row in rows)
    append_observations(directory, protocol, observations)
    return len(observations)


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _strategy_registry():
    """Load static strategy definitions only; this reads no environment or accounts."""
    config_path = _repository_root() / "config" / "strategies.yaml"
    config = StrategiesConfig.model_validate(yaml.safe_load(config_path.read_text(encoding="utf-8")))
    return build_strategy_registry(config.enabled)


def evaluate_registered_round(directory: Path) -> tuple[CandidateResult, ...]:
    """Run the retained causal evaluator with no market request or execution path."""
    protocol = load_round_protocol(directory)
    observations = load_observations(directory)
    quality = summarize_quality(observations, protocol)
    return evaluate_round(protocol, observations, quality, _strategy_registry(), directory=Path(directory))


def _recent_candidate_results(directory: Path) -> tuple[CandidateResult, ...]:
    """Read every retained record; only the later display projection is bounded."""
    return validate_retained_candidate_results(Path(directory), load_round_protocol(directory))


def _candidate_snapshot(result: CandidateResult) -> dict[str, Any]:
    """Return a deliberately small display record, not an executable payload."""
    metrics = result.sealed_test
    return {
        "symbol": result.candidate.symbol,
        "strategy_id": result.candidate.strategy_id,
        "direction": result.candidate.direction,
        "status": result.status.value,
        "paper_only": True,
        "qualification_status": "unqualified",
        "reasons": list(result.reasons),
        "sealed_metrics": {
            "net_return": str(metrics.net_return),
            "stressed_net_return": str(metrics.stressed_net_return),
            "lower_edge": str(metrics.lower_edge) if metrics.lower_edge is not None else None,
            "trade_count": metrics.trade_count,
            "maximum_drawdown": str(metrics.maximum_drawdown),
            "coverage": str(metrics.coverage),
        },
    }


def _bounded_string(value: Any, field: str, *, maximum: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value.encode("utf-8")) > maximum:
        raise ValueError(f"native wire {field} must be a nonempty string of at most {maximum} bytes")
    return value


def _bounded_reasons(value: Any, field: str) -> list[str]:
    if not isinstance(value, (list, tuple)) or len(value) > 16:
        raise ValueError(f"native wire {field} must contain at most 16 reasons")
    return [_bounded_string(reason, f"{field} reason") for reason in value]


def _numeric_string(value: Any, field: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    text = _bounded_string(value, field, maximum=64)
    try:
        numeric = float(text)
    except ValueError as error:
        raise ValueError(f"native wire {field} must be finite") from error
    if not math.isfinite(numeric):
        raise ValueError(f"native wire {field} must be finite")
    return text


def _bounded_fraction(value: Any, field: str) -> str:
    text = _numeric_string(value, field)
    assert text is not None
    if not 0 <= float(text) <= 1:
        raise ValueError(f"native wire {field} must be between zero and one")
    return text


def _native_round_payload(report: RoundReport) -> dict[str, Any]:
    """Project a retained report into the exact, bounded native import schema."""

    def native_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
        expected = {
            "symbol",
            "strategy_id",
            "direction",
            "status",
            "paper_only",
            "qualification_status",
            "reasons",
            "sealed_metrics",
        }
        if set(candidate) != expected:
            raise ValueError("native wire candidate contains unsupported fields")
        symbol = _bounded_string(candidate["symbol"], "symbol")
        if symbol not in {"BTCUSDT", "ETHUSDT"}:
            raise ValueError("native wire symbol is unsupported")
        strategy_id = _bounded_string(candidate["strategy_id"], "strategyId")
        direction = candidate["direction"]
        if direction not in {"long", "abstain"}:
            raise ValueError("native wire direction is unsupported")
        status = candidate["status"]
        if status not in {item.value for item in RoundStatus}:
            raise ValueError("native wire candidate status is unsupported")
        if candidate["paper_only"] is not True or candidate["qualification_status"] != "unqualified":
            raise ValueError("native wire candidate must remain unqualified paper-only")
        metrics = candidate["sealed_metrics"]
        if not isinstance(metrics, Mapping) or set(metrics) != {
            "net_return", "stressed_net_return", "lower_edge", "trade_count", "maximum_drawdown", "coverage"
        }:
            raise ValueError("native wire sealedMetrics contains unsupported fields")
        trade_count = metrics["trade_count"]
        if type(trade_count) is not int or not 0 <= trade_count <= 1_000_000:
            raise ValueError("native wire tradeCount must be a bounded integer")
        return {
            "symbol": symbol,
            "strategyId": strategy_id,
            "direction": direction,
            "status": status,
            "paperOnly": True,
            "qualificationStatus": "unqualified",
            "reasons": _bounded_reasons(candidate["reasons"], "candidate reasons"),
            "sealedMetrics": {
                "netReturn": _numeric_string(metrics["net_return"], "netReturn"),
                "stressedNetReturn": _numeric_string(metrics["stressed_net_return"], "stressedNetReturn"),
                "lowerEdge": _numeric_string(metrics["lower_edge"], "lowerEdge", nullable=True),
                "tradeCount": trade_count,
                "maximumDrawdown": _bounded_fraction(metrics["maximum_drawdown"], "maximumDrawdown"),
                "coverage": _bounded_fraction(metrics["coverage"], "coverage"),
            },
        }

    round_id = _bounded_string(report.round_id, "roundId")
    protocol_hash = report.protocol_hash
    if not isinstance(protocol_hash, str) or len(protocol_hash) != 64 or protocol_hash != protocol_hash.lower() or any(
        character not in "0123456789abcdef" for character in protocol_hash
    ):
        raise ValueError("native wire protocolHash must be a lowercase SHA-256 hash")
    status = report.status.value if isinstance(report.status, RoundStatus) else report.status
    if status not in {item.value for item in RoundStatus}:
        raise ValueError("native wire status is unsupported")
    if report.paper_only is not True or report.qualification_status != "unqualified":
        raise ValueError("native wire report must remain unqualified paper-only")
    if len(report.candidates) > 100:
        raise ValueError("native wire candidates must contain at most 100 candidates")
    payload = {
        "roundId": round_id,
        "protocolHash": protocol_hash,
        "status": status,
        "paperOnly": True,
        "qualificationStatus": "unqualified",
        "reasons": _bounded_reasons(report.reasons, "reasons"),
        "candidates": [native_candidate(candidate) for candidate in report.candidates],
    }
    if set(payload) != _WIRE_ROOT_KEYS:
        raise ValueError("native wire root contains unsupported fields")
    return payload


def build_round_report(directory: Path) -> RoundReport:
    """Build a bounded, fail-closed snapshot from retained Round 2 evidence only."""
    protocol = load_round_protocol(directory)
    evidence_error: str | None = None
    try:
        results = _recent_candidate_results(directory)
    except ValueError as error:
        results, evidence_error = (), str(error)
    if evidence_error is not None:
        status, reasons = RoundStatus.REJECTED, (evidence_error,)
    elif not results:
        status, reasons = RoundStatus.INSUFFICIENT_DATA, ("not_evaluated",)
    elif all(result.status == RoundStatus.INSUFFICIENT_DATA for result in results):
        status, reasons = RoundStatus.INSUFFICIENT_DATA, ("insufficient_data",)
    elif any(result.status == RoundStatus.EXPERIMENTAL_PAPER_ONLY for result in results):
        status, reasons = RoundStatus.EXPERIMENTAL_PAPER_ONLY, ("experimental_paper_only",)
    else:
        status, reasons = RoundStatus.REJECTED, ("all_candidates_rejected",)
    return RoundReport(
        round_id=protocol.round_id,
        protocol_hash=protocol.identity_hash,
        status=status,
        reasons=reasons,
        candidates=tuple(_candidate_snapshot(result) for result in results),
    )


def _write_atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True, separators=(",", ":"), allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def write_round_report(directory: Path) -> Path:
    """Atomically publish the bounded paper-only app snapshot for this round."""
    report = build_round_report(directory)
    path = Path(directory) / SUMMARY_FILE
    _write_atomic_json(path, _native_round_payload(report))
    return path


__all__ = [
    "PremiumProviderAdapter",
    "UnconfiguredPremiumProviderAdapter",
    "build_round_report",
    "evaluate_registered_round",
    "ingest_file",
    "parse_utc",
    "register_default_round",
    "write_round_report",
]
