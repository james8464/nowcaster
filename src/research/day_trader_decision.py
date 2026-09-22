"""Fixed context gates and immutable evidence for paper research postures."""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.research.day_trader_context import MarketContextSnapshot, Regime
from src.research.round_two_contracts import _utc
from src.research.round_two_registry import _write_first_manifest, append_jsonl_fsync, jsonl_writer_lock
from src.research.trend_advisor import TrendAdvisorSuggestion
from src.strategies.types import canonical_hash

CONTEXT_REPORTS_FILE = "day-trader-context-reports.jsonl"
CONTEXT_SUMMARY_FILE = "day-trader-context-summary.json"
DECISION_POLICY_HASH = canonical_hash(
    {
        "version": "day-trader-decision-v1",
        "timeframes": [1, 5, 15],
        "minimum_directional_efficiency": "0.5",
        "require_all_up": True,
        "require_calendar": True,
        "require_book": True,
    }
)


def _regime(context: MarketContextSnapshot | None) -> Regime:
    if context is None:
        return Regime.UNKNOWN
    if {"volatility_exceeds_maximum", "abnormal_range"} & set(context.exclusions):
        return Regime.VOLATILE
    if {"spread_exceeds_maximum", "liquidity_unavailable"} & set(context.exclusions):
        return Regime.ILLIQUID
    if any(trend.direction == "unavailable" or trend.strength is None for trend in context.trends):
        return Regime.UNKNOWN
    if not all(trend.direction == "up" and trend.strength >= Decimal("0.5") for trend in context.trends):
        return Regime.RANGE
    return Regime.TREND


def _gated(advisor, context, now, invalid_context):
    reasons = [] if advisor.posture == "long_research" else list(advisor.reasons)
    regime = _regime(context)
    expiry = advisor.expires_at
    available = advisor.available_at
    if now < advisor.decision_at:
        reasons.append("decision_unavailable")
    if now >= expiry:
        reasons.append("advisor_expired")
    if context is None:
        reasons.append("context_invalid" if invalid_context else "context_missing")
    else:
        if advisor.protocol_hash != context.protocol_hash:
            reasons.append("context_protocol_mismatch")
        if advisor.source_hash != context.source_identity_hash:
            reasons.append("context_source_mismatch")
        if advisor.symbol != context.symbol:
            reasons.append("context_symbol_mismatch")
        if advisor.decision_at != context.decision_at:
            reasons.append("context_decision_mismatch")
        if context.available_at > now:
            reasons.append("context_unavailable")
        if now >= context.expires_at:
            reasons.append("context_expired")
        if not context.calendar_hash or context.calendar_blackout is None:
            reasons.append("context_calendar_unavailable")
        if context.calendar_blackout:
            reasons.append("context_event_blackout")
        if not context.source_hashes or any(
            getattr(context, key) is None
            for key in ("realized_volatility_bps", "atr_normalized_range", "spread_bps", "quote_imbalance")
        ):
            reasons.append("context_features_unavailable")
        if regime != Regime.TREND:
            reasons.append(f"context_{regime.value}")
        reasons.extend(context.exclusions)
        expiry = min(expiry, context.expires_at)
        # A mismatched/future snapshot is retained for rejection evidence, never
        # used to assert that the original advisor saw a later input.
        if context.available_at <= advisor.decision_at:
            available = max(available, context.available_at) if available else context.available_at
    reasons = tuple(dict.fromkeys(reasons))[:64]
    values = advisor.model_dump()
    values.update(expires_at=expiry, available_at=available)
    if reasons:
        values.update(
            posture="stand_aside", entry_low=None, entry_high=None, invalidation=None, target=None, reasons=reasons[:16]
        )
    return TrendAdvisorSuggestion.model_validate(values), regime, reasons


class DecisionContextReport(BaseModel):
    """Retains the exact advisor input, context, gated output and their binding."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    paper_only: Literal[True] = True
    protocol_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision_policy_hash: Literal[DECISION_POLICY_HASH] = DECISION_POLICY_HASH
    evaluated_at: datetime
    advisor: TrendAdvisorSuggestion
    advisor_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    context: MarketContextSnapshot | None
    invalid_context: bool = False
    regime: Regime
    suggestion: TrendAdvisorSuggestion
    reasons: tuple[str, ...] = Field(max_length=64)
    report_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def verify_binding(self):
        _utc(self.evaluated_at, "decision report timestamp")
        if self.protocol_hash != self.advisor.protocol_hash:
            raise ValueError("decision report protocol mismatch")
        if self.advisor_hash != canonical_hash(self.advisor.model_dump(mode="json")):
            raise ValueError("advisor identity mismatch")
        if self.invalid_context and self.context is not None:
            raise ValueError("invalid context cannot supply features")
        suggestion, regime, reasons = _gated(self.advisor, self.context, self.evaluated_at, self.invalid_context)
        if (self.suggestion, self.regime, self.reasons) != (suggestion, regime, reasons):
            raise ValueError("decision gate binding mismatch")
        if self.report_hash != canonical_hash(self.model_dump(mode="json", exclude={"report_hash"})):
            raise ValueError("decision report hash mismatch")
        return self


def gate_suggestion(
    suggestion: TrendAdvisorSuggestion, context: MarketContextSnapshot | None, now: datetime
) -> DecisionContextReport:
    now = _utc(now, "decision evaluation")
    advisor = TrendAdvisorSuggestion.model_validate(suggestion.model_dump())
    invalid = False
    if context is not None:
        try:
            context = MarketContextSnapshot.model_validate(context.model_dump())
        except (ValueError, AttributeError):
            context, invalid = None, True
    gated, regime, reasons = _gated(advisor, context, now, invalid)
    payload = dict(
        schema_version=1,
        paper_only=True,
        protocol_hash=advisor.protocol_hash,
        decision_policy_hash=DECISION_POLICY_HASH,
        evaluated_at=now,
        advisor=advisor,
        advisor_hash=canonical_hash(advisor.model_dump(mode="json")),
        context=context,
        invalid_context=invalid,
        regime=regime,
        suggestion=gated,
        reasons=reasons,
    )
    unsigned = DecisionContextReport.model_construct(**payload, report_hash="0" * 64)
    payload["report_hash"] = canonical_hash(unsigned.model_dump(mode="json", exclude={"report_hash"}))
    return DecisionContextReport.model_validate(payload)


def retain_context_reports(
    directory: Path, reports: tuple[DecisionContextReport, ...], *, protocol_hash: str, context_protocol_hash: str
) -> None:
    """Append only validated evidence under one fixed decision/context policy."""
    identity = dict(
        protocol_hash=protocol_hash,
        context_protocol_hash=context_protocol_hash,
        decision_policy_hash=DECISION_POLICY_HASH,
    )
    manifest = json.dumps(identity, sort_keys=True, separators=(",", ":")) + "\n"
    path = directory / "day-trader-context-manifest.json"
    if not path.exists() and (directory / CONTEXT_REPORTS_FILE).exists():
        raise ValueError("retained context has no manifest")
    if (directory / CONTEXT_SUMMARY_FILE).exists() and not (directory / CONTEXT_REPORTS_FILE).exists():
        raise ValueError("retained context history missing")
    if not _write_first_manifest(path, manifest.encode()) and path.read_text() != manifest:
        raise ValueError("context policy changed; register a new round")

    def checked(report):
        report = DecisionContextReport.model_validate(report.model_dump())
        if report.protocol_hash != protocol_hash or (
            report.context is not None and report.context.context_protocol_hash != context_protocol_hash
        ):
            raise ValueError("retained context protocol mismatch")
        return report

    path = directory / CONTEXT_REPORTS_FILE
    with jsonl_writer_lock(path):
        if path.exists():
            data = path.read_bytes()
            if data and not data.endswith(b"\n"):
                raise ValueError("unterminated context report")
            for line in data.splitlines():
                checked(DecisionContextReport.model_validate_json(line))
        append_jsonl_fsync(path, [checked(report).model_dump(mode="json") for report in reports], writer_lock_held=True)


def load_context_reports(
    directory: Path, *, protocol_hash: str, context_protocol_hash: str
) -> tuple[DecisionContextReport, ...]:
    """Validate the entire retained history and its manifest before selection."""
    identity = dict(
        protocol_hash=protocol_hash,
        context_protocol_hash=context_protocol_hash,
        decision_policy_hash=DECISION_POLICY_HASH,
    )
    if json.loads((directory / "day-trader-context-manifest.json").read_text()) != identity:
        raise ValueError("context manifest mismatch")
    data = (directory / CONTEXT_REPORTS_FILE).read_bytes()
    if not data or not data.endswith(b"\n"):
        raise ValueError("missing or unterminated context report")
    reports = tuple(DecisionContextReport.model_validate_json(line) for line in data.splitlines())
    for report in reports:
        if report.protocol_hash != protocol_hash or (
            report.context is not None and report.context.context_protocol_hash != context_protocol_hash
        ):
            raise ValueError("retained context protocol mismatch")
    return reports
