"""Separate, explicitly unqualified report from immutable practice paper events."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
import json
from typing import Literal

from pydantic import Field, model_validator

from src.intraday.capture_quality import summarize_capture
from src.intraday.contracts import FrozenModel
from src.intraday.experimental_round import ExperimentalRoundManifest
from src.intraday.journal import PaperJournal
from src.intraday.report import PaperDecisionReport, aggregate_reports, build_report
from src.intraday.session_journal import SessionJournal
from src.strategies.types import canonical_hash


class ExperimentalPaperReport(FrozenModel):
    schema_version: Literal[1] = 1
    paper_only: Literal[True] = True
    qualification: Literal["unqualified"] = "unqualified"
    round_id: str
    cohort_id: str
    cohort_identity_hash: str
    performance: PaperDecisionReport
    attempted_entries: int = Field(ge=0)
    rejected_entries: int = Field(ge=0)
    expected_eligible_intervals: int = Field(ge=0)
    covered_eligible_intervals: int = Field(ge=0)
    sampled_intervals: int = Field(ge=0)
    missing_eligible_days: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def consistent(self):
        if self.performance.round_id != self.round_id:
            raise ValueError("experimental report identity mismatch")
        if self.covered_eligible_intervals > self.expected_eligible_intervals:
            raise ValueError("covered intervals exceed declared session")
        if self.rejected_entries > self.attempted_entries:
            raise ValueError("paper rejections exceed attempts")
        return self


def _cohort_identity(manifest: ExperimentalRoundManifest) -> str:
    return canonical_hash(manifest.model_dump(mode="json", exclude={"round_id", "session_open", "session_close"}))


def _strict_slots(manifest: ExperimentalRoundManifest, events: tuple[dict, ...], as_of: datetime,
                  *, ignore_after_as_of: bool = False) -> tuple[int, int]:
    end = min(as_of, manifest.session_close)
    if end < manifest.session_open:
        return 0, 0
    slots = int((end - manifest.session_open) // timedelta(minutes=5))
    observed: dict[int, list[datetime]] = {index: [] for index in range(slots)}
    gaps: set[int] = set()
    for event in events:
        if datetime.fromisoformat(event["at"]) > as_of:
            if ignore_after_as_of:
                continue
            raise ValueError("capture has future event relative to experimental report")
        payload = event["payload"]
        if payload.get("broker_symbol") != manifest.instrument.broker_symbol:
            continue
        when = datetime.fromisoformat(event["at"])
        index = int((when - manifest.session_open) // timedelta(minutes=5))
        if event["kind"] == "gap" and 0 <= index < slots:
            gaps.add(index)
        if event["kind"] != "quote" or payload.get("tradeable") != "True":
            continue
        sampled_at = datetime.fromisoformat(payload["observed_at"])
        index = int((sampled_at - manifest.session_open) // timedelta(minutes=5))
        if not 0 <= index < slots or not sampled_at <= when or when - sampled_at > timedelta(seconds=5):
            continue
        observed[index].append(sampled_at)
    covered = 0
    for index, samples in observed.items():
        if index in gaps or not samples:
            continue
        samples.sort()
        start = manifest.session_open + index * timedelta(minutes=5)
        end = start + timedelta(minutes=5)
        if (samples[0] - start <= timedelta(seconds=15)
                and end - samples[-1] <= timedelta(seconds=15)
                and all(right - left <= timedelta(seconds=30)
                        for left, right in zip(samples, samples[1:], strict=False))):
            covered += 1
    return slots, covered


def build_experimental_report(
    manifest: ExperimentalRoundManifest, paper: PaperJournal, session: SessionJournal, *, as_of: datetime,
    ignore_after_as_of: bool = False,
) -> ExperimentalPaperReport:
    identity = manifest.model_dump(mode="json")
    if paper.protocol_hash != canonical_hash(identity):
        raise ValueError("paper journal identity differs from experimental manifest")
    if json.loads(session.manifest.read_text(encoding="utf-8")) != identity:
        raise ValueError("quote journal identity differs from experimental manifest")
    start = min(as_of, manifest.session_open)
    base = build_report(
        paper, round_id=manifest.round_id, started_at=start, as_of=as_of,
        session_journal=session, dedup_session_no_trades=True, ignore_after_as_of=ignore_after_as_of,
    )
    events = session.events()
    slots, covered = _strict_slots(manifest, events, as_of, ignore_after_as_of=ignore_after_as_of)
    sampled = summarize_capture(manifest.instrument, manifest.session_open,
                                max(manifest.session_open, min(as_of, manifest.session_close)), events)
    cohort_hash = _cohort_identity(manifest)
    base = PaperDecisionReport.model_validate({
        **base.model_dump(), "declared_session_end_at": manifest.session_close,
        "rule_identity_hash": cohort_hash,
        "coverage_status": "measured" if slots else "not_measured",
        "account_quote_coverage": Decimal(covered) / slots if slots else None,
        "expected_quote_intervals": slots if slots else None,
        "covered_quote_intervals": covered if slots else None,
        "eligible_session_gap_count": sum(
            event["kind"] == "gap" and manifest.session_open <= datetime.fromisoformat(event["at"]) < min(as_of, manifest.session_close)
            for event in events
        ),
        "warning": "Unqualified experimental paper trades use hypothetical fills; no real-money profitability is established.",
    })
    paper_events = paper.events()
    attempts = sum(event.kind == "planned" and event.occurred_at <= as_of for event in paper_events)
    rejections = sum(event.kind == "no_trade" and "decision_source_key" in event.payload
                     and event.occurred_at <= as_of for event in paper_events)
    return ExperimentalPaperReport(
        round_id=manifest.round_id, cohort_id=manifest.cohort_id,
        cohort_identity_hash=cohort_hash, performance=base,
        attempted_entries=attempts, rejected_entries=rejections,
        expected_eligible_intervals=slots, covered_eligible_intervals=covered,
        sampled_intervals=sampled.covered_intervals,
    )


def aggregate_experimental_cohort(
    reports: tuple[ExperimentalPaperReport, ...], *, as_of: datetime
) -> ExperimentalPaperReport:
    if not reports:
        raise ValueError("cohort requires retained experimental rounds")
    ordered = sorted(reports, key=lambda report: report.performance.started_at)
    first = ordered[0]
    if len({report.round_id for report in ordered}) != len(ordered):
        raise ValueError("duplicate experimental round")
    if any(report.cohort_id != first.cohort_id or report.cohort_identity_hash != first.cohort_identity_hash
           for report in ordered):
        raise ValueError("experimental cohort identity changed")
    dates = [report.performance.started_at.date() for report in ordered]
    if len(set(dates)) != len(dates):
        raise ValueError("overlapping experimental cohort day")
    missing_days = (dates[-1] - dates[0]).days + 1 - len(dates)
    # Without a verified holiday calendar we conservatively count every
    # absent calendar day as eligible rather than hiding missing coverage.
    declared_per_day = max(report.expected_eligible_intervals for report in ordered)
    expected = sum(report.expected_eligible_intervals for report in ordered) + missing_days * declared_per_day
    covered = sum(report.covered_eligible_intervals for report in ordered)
    performance = aggregate_reports(tuple(report.performance for report in ordered), as_of=as_of)
    round_id = f"cohort:{first.cohort_id}"
    performance = PaperDecisionReport.model_validate({
        **performance.model_dump(), "round_id": round_id,
        "expected_quote_intervals": expected if expected else None,
        "covered_quote_intervals": covered if expected else None,
        "account_quote_coverage": Decimal(covered) / expected if expected else None,
        "coverage_status": "measured" if expected else "not_measured",
        "daily_block_lower_95_gbp": None if missing_days else performance.daily_block_lower_95_gbp,
        "warning": "Unqualified experimental cohort; missing days and every retained loss remain visible.",
    })
    return ExperimentalPaperReport(
        round_id=round_id, cohort_id=first.cohort_id,
        cohort_identity_hash=first.cohort_identity_hash, performance=performance,
        attempted_entries=sum(report.attempted_entries for report in ordered),
        rejected_entries=sum(report.rejected_entries for report in ordered),
        expected_eligible_intervals=expected, covered_eligible_intervals=covered,
        sampled_intervals=sum(report.sampled_intervals for report in ordered),
        missing_eligible_days=missing_days,
    )
