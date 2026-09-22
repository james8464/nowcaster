"""Read-only, bounded projection of validated research evidence for macOS."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from src.research.day_trader_decision import CONTEXT_REPORTS_FILE, load_context_reports
from src.research.day_trader_lifecycle import LIFECYCLE_POLICY_HASH, PaperLifecycle, advance_lifecycle
from src.research.round_two_contracts import _utc
from src.strategies.types import canonical_hash


def _completed(directory, protocol_hash, context_protocol_hash, now):
    path = directory / "paper-lifecycles.jsonl"
    if not path.exists():
        return []
    manifest = json.loads((directory / "paper-lifecycles-manifest.json").read_text())
    if manifest != dict(protocol_hash=protocol_hash, policy_hash=LIFECYCLE_POLICY_HASH):
        raise ValueError("lifecycle manifest mismatch")
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        raise ValueError("unterminated lifecycle evidence")
    latest = {}
    holding = None
    for line in data.splitlines():
        item = PaperLifecycle.model_validate_json(line)
        if (
            item.origin_report.protocol_hash != protocol_hash
            or item.origin_report.context.context_protocol_hash != context_protocol_hash
            or item.created_at > now
            or (item.completed_at and item.completed_at > now)
            or (item.last_observation and item.last_observation.evaluated_at > now)
        ):
            raise ValueError("lifecycle identity or clock mismatch")
        if holding is not None and holding != item.maximum_holding_seconds:
            raise ValueError("lifecycle holding policy changed")
        holding = item.maximum_holding_seconds
        previous = latest.get(item.lifecycle_hash)
        if previous is None:
            if item.revision != 0:
                raise ValueError("missing lifecycle origin")
        elif (
            item.revision != previous.revision + 1
            or item.previous_record_hash != previous.record_hash
            or item.last_observation is None
            or advance_lifecycle(previous, item.last_observation) != item
        ):
            raise ValueError("invalid lifecycle transition")
        latest[item.lifecycle_hash] = item
    completed = sorted((x for x in latest.values() if x.completed_at), key=lambda x: x.completed_at)
    return [
        dict(
            symbol=x.origin_report.suggestion.symbol,
            protocol_hash=protocol_hash,
            context_protocol_hash=context_protocol_hash,
            lifecycle_hash=x.lifecycle_hash,
            record_hash=x.record_hash,
            origin_report_hash=x.origin_report.report_hash,
            created_at=x.created_at.isoformat(),
            completed_at=x.completed_at.isoformat(),
            exit_reason=x.exit_reason,
            revision=x.revision,
        )
        for x in completed[-30:]
    ]


def decision_presentation(directory: Path, *, protocol_hash: str, context_protocol_hash: str, now: datetime) -> dict:
    """Validate whole histories; never create files or treat outcomes as signals."""
    now = _utc(now, "presentation timestamp")
    for identity in (protocol_hash, context_protocol_hash):
        if len(identity) != 64 or any(x not in "0123456789abcdef" for x in identity):
            raise ValueError("invalid presentation identity")
    directory = Path(directory)
    for name in (CONTEXT_REPORTS_FILE, "paper-lifecycles.jsonl"):
        path = directory / name
        if path.exists() and path.stat().st_size > 64 * 1024 * 1024:
            raise ValueError("presentation history exceeds read bound")
    reports = ()
    context_files = (CONTEXT_REPORTS_FILE, "day-trader-context-summary.json", "day-trader-context-manifest.json")
    if any((directory / name).exists() for name in context_files):
        reports = load_context_reports(
            directory, protocol_hash=protocol_hash, context_protocol_hash=context_protocol_hash
        )
    latest = {}
    for report in reports:
        if report.evaluated_at > now or report.suggestion.decision_at > now:
            raise ValueError("future decision evidence")
        previous = latest.get(report.suggestion.symbol)
        if previous is None or report.evaluated_at >= previous.evaluated_at:
            latest[report.suggestion.symbol] = report
    contexts = []
    for report in latest.values():
        suggestion, context = report.suggestion, report.context
        if now >= suggestion.expires_at or context is None or now >= context.expires_at:
            continue
        # Rejected foreign context is retained evidence, never a current display.
        if (
            context.protocol_hash != protocol_hash
            or context.symbol != suggestion.symbol
            or context.source_identity_hash != suggestion.source_hash
            or context.decision_at != suggestion.decision_at
            or context.available_at > now
        ):
            continue
        contexts.append(
            dict(
                symbol=suggestion.symbol,
                protocol_hash=protocol_hash,
                context_protocol_hash=context_protocol_hash,
                report_hash=report.report_hash,
                feature_hash=context.feature_hash,
                decision_at=suggestion.decision_at.isoformat(),
                available_at=context.available_at.isoformat(),
                expires_at=suggestion.expires_at.isoformat(),
                regime=report.regime.value,
                posture=suggestion.posture,
                trends=[
                    dict(
                        minutes=t.timeframe_minutes,
                        direction=t.direction,
                        strength=str(t.strength) if t.strength is not None else None,
                    )
                    for t in context.trends
                ],
                spread_bps=str(context.spread_bps) if context.spread_bps is not None else None,
                volatility_bps=str(context.realized_volatility_bps)
                if context.realized_volatility_bps is not None
                else None,
                quote_imbalance=str(context.quote_imbalance) if context.quote_imbalance is not None else None,
                session=context.session,
                calendar_blackout=context.calendar_blackout,
                reasons=list(report.reasons),
            )
        )
    payload = dict(
        schema_version=1,
        paper_only=True,
        protocol_hash=protocol_hash,
        context_protocol_hash=context_protocol_hash,
        generated_at=now.isoformat(),
        contexts=contexts,
        outcomes=_completed(directory, protocol_hash, context_protocol_hash, now),
    )
    payload["content_hash"] = canonical_hash(payload)
    return payload
