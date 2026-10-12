"""Immutable identity for explicitly unqualified OANDA practice paper rounds."""

from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from src.intraday.contracts import FrozenModel, InstrumentSpec
from src.intraday.paper import PaperExecutionCosts, PaperRiskPolicy

_INVENTORY = {
    "DE30_EUR": ("germany40", "cfd", "EUR"),
    "SPX500_USD": ("us500", "cfd", "USD"),
    "EUR_USD": ("eurusd", "margin_fx", "USD"),
    "WTICO_USD": ("wti", "cfd", "USD"),
}
_STRATEGIES = {"opening_range_15", "opening_range_30", "trend_pullback", "range_reversion"}
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{2,100}\Z")


class ExperimentalRoundManifest(FrozenModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    round_id: str
    cohort_id: str
    account_feed_hash: str
    instrument: InstrumentSpec
    strategy_id: str
    direction: Literal["long", "short"]
    session_open: datetime
    session_close: datetime
    calendar_hash: str
    costs: PaperExecutionCosts
    cost_source: str
    cost_source_hash: str
    verified_at: datetime
    account_currency: str
    margin_rate: Decimal = Field(gt=0, le=1)
    unit_step: Decimal = Field(gt=0)
    initial_paper_cash: Decimal = Field(gt=0)
    code_hash: str
    qualification: Literal["unqualified"] = "unqualified"
    risk_policy: PaperRiskPolicy = Field(default_factory=PaperRiskPolicy)

    @field_validator("round_id", "cohort_id")
    @classmethod
    def safe_id(cls, value: str) -> str:
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("round and cohort IDs must be safe lower-case identifiers")
        return value

    @field_validator("account_feed_hash", "calendar_hash", "cost_source_hash", "code_hash")
    @classmethod
    def valid_hash(cls, value: str) -> str:
        if not _HASH.fullmatch(value):
            raise ValueError("identity must be a SHA-256 hex digest")
        return value

    @model_validator(mode="after")
    def complete_evidence(self):
        if self.instrument.provider != "oanda_practice" or _INVENTORY.get(self.instrument.broker_symbol) != (
            self.instrument.market,
            self.instrument.product,
            self.instrument.quote_currency,
        ):
            raise ValueError("instrument is not an exact supported practice product")
        if self.strategy_id not in _STRATEGIES:
            raise ValueError("unknown experimental strategy")
        if self.session_open >= self.session_close or self.session_open.date() != self.session_close.date():
            raise ValueError("session window must fit one UTC day")
        if self.verified_at > self.session_open:
            raise ValueError("cost verification must precede session")
        if not self.costs.verified or not self.cost_source.strip():
            raise ValueError("source-backed verified execution costs required")
        if self.account_currency != "GBP":
            raise ValueError("experimental paper equity and reporting require a GBP account")
        return self


def _bytes(manifest: ExperimentalRoundManifest) -> bytes:
    return (manifest.model_dump_json(indent=2) + "\n").encode("utf-8")


def register_experimental_round(
    root: Path,
    manifest: ExperimentalRoundManifest,
    *,
    cost_evidence: bytes,
    at: datetime | None = None,
) -> Path:
    """Create once, or prove that an existing manifest is byte-for-byte identical."""
    now = at or datetime.now(UTC)
    root = Path(root).expanduser().resolve()
    if {"ProspectiveStudies", "live-paper-study"} & set(root.parts):
        raise ValueError("protected prospective study path")
    if now.tzinfo is None or now.utcoffset().total_seconds() != 0 or now >= manifest.session_open:
        raise ValueError("experiment must be registered before its UTC session opens")
    if (
        not cost_evidence
        or len(cost_evidence) > 5 * 1024 * 1024
        or hashlib.sha256(cost_evidence).hexdigest() != manifest.cost_source_hash
    ):
        raise ValueError("cost evidence is absent or differs from its frozen hash")
    directory = root / "ExperimentalRounds" / manifest.round_id
    directory.mkdir(parents=True, exist_ok=True)
    evidence_path = directory / "cost-evidence.bin"
    try:
        with evidence_path.open("xb") as handle:
            handle.write(cost_evidence)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if evidence_path.read_bytes() != cost_evidence:
            raise ValueError("retained cost evidence differs; use a new round ID") from None
    path = directory / "manifest.json"
    content = _bytes(manifest)
    try:
        with path.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except FileExistsError:
        if path.read_bytes() != content:
            raise ValueError("retained experiment manifest differs; use a new round ID") from None
    return path


def load_experimental_round(
    path: Path, *, account_feed_hash: str, code_hash: str, at: datetime
) -> ExperimentalRoundManifest:
    manifest = ExperimentalRoundManifest.model_validate_json(path.read_bytes())
    evidence = path.parent / "cost-evidence.bin"
    if not evidence.is_file() or hashlib.sha256(evidence.read_bytes()).hexdigest() != manifest.cost_source_hash:
        raise ValueError("retained cost evidence is missing or changed")
    if manifest.account_feed_hash != account_feed_hash or manifest.code_hash != code_hash:
        raise ValueError("experimental account or bundled helper identity mismatch")
    if at.tzinfo is None or at.utcoffset().total_seconds() != 0:
        raise ValueError("current time must be UTC")
    return manifest
